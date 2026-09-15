"""Optional, local NNsight row capture over an already-loaded model.

This adapter deliberately does not load/tokenize/generate, use NDIF, or expose
arbitrary user code. NNsight instrumentation is temporary. The pinned version
is tested against tiny randomly initialized CPU models, not certified for Muse.
"""
import importlib.metadata
import importlib.util
from contextlib import contextmanager

TESTED_VERSION = '0.7.0'
INSTALL = 'uv pip install --python .venv/bin/python -r requirements/nnsight.txt'
_ATTRS = ('forward', '__path__', '__nnsight_forward__')
_HOOKS = ('_forward_hooks', '_forward_pre_hooks', '_forward_hooks_with_kwargs',
          '_forward_hooks_always_called', '_forward_pre_hooks_with_kwargs')


def availability(adapter=None):
    result = dict(id='nnsight', label='NNsight capture (experimental)', available=False,
                  reason='Install the optional package on your compute worker: '+INSTALL,
                  version=None)
    if importlib.util.find_spec('nnsight') is None:
        return result
    try:
        result['version'] = importlib.metadata.version('nnsight')
    except importlib.metadata.PackageNotFoundError:
        return result
    if result['version'] != TESTED_VERSION:
        result['reason'] = f'NNsight {TESTED_VERSION} is the tested capture API; found {result["version"]}. Use an isolated worker with the pinned optional requirements.'
        return result
    if adapter is not None:
        if adapter.model.training:
            result['reason'] = 'Put the attached model in evaluation mode before inspection.'
            return result
        if any(hasattr(m, '__nnsight_forward__') for m in adapter.model.modules()):
            result['reason'] = 'This model is already instrumented by NNsight. Attach an unwrapped model to avoid overlapping instrumentation.'
            return result
    result.update(available=True, reason='Local read-only capture of the attached model. Validated on tiny CPU test models; Muse compatibility and speed are not established.')
    return result


def require(adapter):
    state = availability(adapter)
    if not state['available']:
        raise ValueError(state['reason'])
    try:
        from nnsight import NNsight, save
    except (ImportError, RuntimeError) as exc:
        raise ValueError('The optional NNsight runtime could not load. Install its dependencies in the compute worker; standard capture remains available.') from exc
    return NNsight, save


@contextmanager
def temporary_wrapper(model, wrapper_type):
    """Restore only pinned NNsight instrumentation, including on failed traces.

    Service jobs serialize access to the loaded model. Existing hooks and
    instance-level forward implementations are preserved exactly.
    """
    snapshots = [(module, {key: (key in module.__dict__, module.__dict__.get(key)) for key in _ATTRS},
                  {key: dict(getattr(module, key)) for key in _HOOKS})
                 for module in model.modules()]
    try:
        yield wrapper_type(model)
    finally:
        for module, attrs, hooks in snapshots:
            for key, (existed, value) in attrs.items():
                if existed:
                    setattr(module, key, value)
                else:
                    module.__dict__.pop(key, None)
            for key, values in hooks.items():
                current = getattr(module, key)
                current.clear()
                current.update(values)


def capture(adapter, layers, ids, requested, width, forward_kwargs, check):
    """Return {layer: selected CPU rows}, final CPU row, actual next logits.

    ``requested`` maps validated layer indices to absolute token positions.
    Always obtains final residual/logits for the existing unembedding parity
    check. Full-depth replay remains necessary; this is not a speedup claim.
    """
    import torch
    wrapper_type, save = require(adapter)
    check()
    if ids.ndim != 2 or ids.shape[0] != 1 or not ids.shape[1]:
        raise ValueError('NNsight capture requires one nonempty exact-token prefix.')
    if not 1 <= len(requested) <= 9 or any(type(l) is not int or not 0 <= l < len(layers) for l in requested):
        raise ValueError('NNsight capture requires valid selected decoder layers.')
    if any(not positions or len(positions) > 64 or any(type(p) is not int or not 0 <= p < ids.shape[1] for p in positions)
           for positions in requested.values()):
        raise ValueError('NNsight capture requires 1–64 valid positions per selected layer.')
    names = {id(module): name for name, module in adapter.model.named_modules()}
    if any(id(layers[l]) not in names for l in set(requested) | {len(layers)-1}):
        raise ValueError('Selected decoder blocks do not belong to the attached model.')
    with temporary_wrapper(adapter.model, wrapper_type) as wrapped:
        nodes = {}
        for layer in sorted(set(requested) | {len(layers)-1}):
            node = wrapped
            for part in names[id(layers[layer])].split('.'):
                node = getattr(node, part)
            nodes[layer] = node
        with torch.inference_mode():
            with wrapped.trace(ids, attention_mask=torch.ones_like(ids), use_cache=False, **forward_kwargs):
                rows = {}
                for layer, node in nodes.items():
                    output = node.output
                    hidden = output[0] if isinstance(output, tuple) else output
                    if not isinstance(hidden, torch.Tensor) or hidden.ndim != 3 or hidden.shape[:2] != ids.shape or hidden.shape[-1] != width:
                        raise ValueError('Unexpected NNsight decoder residual shape; use standard capture for this architecture.')
                    if layer in requested:
                        rows[layer] = hidden[0, requested[layer]].detach().float().cpu().clone()
                    if layer == len(layers)-1:
                        final = hidden[0, -1:].detach().float().cpu().clone().save()
                captured = save(rows)
                actual = wrapped.output.logits[0, -1].detach().float().cpu().clone().save()
    check()
    if set(captured) != set(requested) or any(captured[l].shape != (len(requested[l]), width) for l in requested):
        raise ValueError('NNsight returned incomplete selected activation rows.')
    if not all(torch.isfinite(t).all() for t in [*captured.values(), final, actual]):
        raise ValueError('NNsight returned non-finite activation values.')
    return captured, final, actual
