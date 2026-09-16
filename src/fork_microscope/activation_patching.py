"""Bounded, exact-prefix activation interventions with fresh controls.

Method inspired by NNsight's activation-patching tutorial. The execution adapter
uses the already attached native/HF model; it does not require or impersonate
an NNsight backend. Weights and generation configuration are never modified.
"""
from contextlib import contextmanager
import copy
import inspect
import math
import time

from fork_microscope.investigation import capture_sites, digest, inspect_generated, source, whole
from fork_microscope.lens_integration import selected_arms
from fork_microscope.model_preflight import same_model_identity

SCHEMA = 'fork-activation-patch-v1'
ARMS = ('baseline', 'identity', 'patched')
FIELDS = {'source_run_id', 'source_pass_id', 'donor', 'recipient', 'layers',
          'samples', 'cont_max', 'temperature', 'seed', 'selection_rationale', 'max_seconds'}
INTERPRETATION = (
    'A donor state replaces the recipient decoder-block output at the final input '
    'token, once during prefill. Later generation uses the resulting KV cache. '
    'Baseline and identity/self-patch controls use fresh continuations with the same '
    'seed per draw. Outcome frequencies use full-vocabulary sampling, separately '
    'from the retained-branch scan. These post-selected, small-sample differences '
    'are not significance tests or a complete causal explanation of the original '
    'fork. A token/layer readout does not establish hidden intent. Numeric position '
    'alignment is user-selected and does not establish semantic alignment.'
)


def options(adapter):
    out = dict(available=False, backend='native', experimental=True, layer_count=0,
               limits=dict(layers=4, samples=32, cont_max=512, max_seconds=1800),
               site='decoder_block_output_before_final_norm', interpretation=INTERPRETATION)
    if adapter is None:
        return dict(out, reason='Connect compute and attach the exact saved model to test an activation change. Saved artifacts remain viewable without a GPU.')
    try:
        path, layers = capture_sites(adapter)
        if adapter.model.training:
            raise ValueError('Activation patching requires evaluation mode.')
        if getattr(adapter.model.generation_config, 'use_cache', True) is False:
            raise ValueError('Activation patching requires KV-cached generation (use_cache=True).')
        out.update(available=True, layer_count=len(layers), module_path=path, model=dict(adapter.info),
                   reason='Native decoder-block patching is available; preview exact prefixes before running.')
    except (ValueError, AttributeError) as exc:
        out['reason'] = str(exc)
    return out


def build_plan(adapter, result, request, get_investigation=lambda _: None):
    if type(request) is not dict or set(request) != FIELDS or request['source_run_id'] != result['id']:
        raise ValueError('Invalid activation-patching fields or source run.')
    capability = options(adapter)
    if not capability['available']:
        raise ValueError(capability['reason'])
    base = source(adapter, result, request)
    q = copy.deepcopy(request)
    for key, lo, hi in [('samples', 2, 32), ('cont_max', 1, 512), ('seed', 0, 2**31-1), ('max_seconds', 1, 1800)]:
        whole(q[key], key, lo, hi)
    if type(q['temperature']) not in (int, float) or not math.isfinite(q['temperature']) or not .05 <= q['temperature'] <= 2:
        raise ValueError('Temperature must be from 0.05 to 2.')
    if type(q['selection_rationale']) is not str or not 1 <= len(q['selection_rationale'].strip()) <= 2000:
        raise ValueError('Describe why these donor/recipient positions and layers were selected (1–2,000 characters).')
    if type(q['layers']) is not list or not 1 <= len(q['layers']) <= 4:
        raise ValueError('Choose 1–4 decoder layers, patched simultaneously.')
    for layer in q['layers']:
        whole(layer, 'Layer', 0, capability['layer_count']-1)
    if len(set(q['layers'])) != len(q['layers']):
        raise ValueError('Layer indices must be unique.')
    selected = dict(result, _selected_record=result['records'][q['source_pass_id']], _selected_pass=q['source_pass_id'])
    prefixes = {}
    for arm in ('donor', 'recipient'):
        value = q[arm]
        if type(value) is not dict or set(value) != {'selection', 'position'}:
            raise ValueError(f'Choose a saved {arm} trajectory and an inclusive response token position.')
        selection = value['selection']
        if type(selection) is not dict or selection.get('type') not in ('original', 'draw'):
            raise ValueError('Patching currently accepts the original trajectory or one exact saved continuation.')
        resolved = selected_arms(adapter, selected, base, selection, get_investigation)
        if len(resolved) != 1:
            raise ValueError('Select one trajectory per arm.')
        _, prompt, response = resolved[0]
        for ids in (prompt, response):
            if type(ids) is not list or not ids or any(type(t) is not int or not 0 <= t < adapter.info['vocab_size'] for t in ids):
                raise ValueError('Saved trajectory token IDs are missing or invalid.')
        position = whole(value['position'], f'{arm} position', 0, len(response)-1)
        response_prefix = list(response[:position+1])
        ids = list(prompt) + response_prefix
        if any(t in adapter.eos_ids for t in response_prefix):
            raise ValueError('Choose a position before the response termination token.')
        if len(ids) + (q['cont_max'] if arm == 'recipient' else 0) > adapter.info['context_limit']:
            raise ValueError('Selected prefix and continuation cap exceed the model context limit.')
        prefixes[arm] = dict(selection=copy.deepcopy(selection), position=position,
            absolute_position=len(ids)-1, prompt_ids=list(prompt), response_ids=response_prefix,
            input_ids=ids, prefix_sha256=digest(ids), token_id=ids[-1],
            token_text=adapter.tokenizer.decode([ids[-1]], skip_special_tokens=False),
            prefix_text=adapter.tokenizer.decode(response_prefix, skip_special_tokens=False))
    if prefixes['donor']['input_ids'] == prefixes['recipient']['input_ids']:
        raise ValueError('These exact prefixes are identical: their pre-divergence activations are the same. Choose positions after an actual token difference; later different answers do not create earlier different states.')
    return dict(schema=SCHEMA, request=q, model=dict(adapter.info), prefixes=prefixes,
        source_ids_sha256=digest({'prompt_ids':base['prompt_ids'], 'gen_ids':base['gen_ids']}),
        source_base=copy.deepcopy(base), selected_after_inspecting_source=True,
        backend='native', module_path=capability['module_path'], site=capability['site'],
        generation_defaults=adapter.model.generation_config.to_dict(),
        attention_implementation=getattr(adapter.model.config, '_attn_implementation', None),
        position_semantics='Zero-based response token INCLUDED in each prefix; fresh generation starts at the next token. Only the final prefix row is patched once, during KV-cached prefill.',
        sampling=dict(temperature=q['temperature'], top_p=1.0, top_k=0, batch_size=1,
                      branch_filter=False, same_seed_per_draw=True, use_cache=True),
        answers=copy.deepcopy(result.get('base_config', {}).get('answers')),
        outcome_rule=copy.deepcopy(result.get('base_config', {}).get('outcome_rule')),
        continuations=3*q['samples'], max_new_tokens=3*q['samples']*q['cont_max'],
        time_limit=dict(seconds=q['max_seconds'], enforcement='cooperative between forward calls and draws; an in-flight kernel cannot be preempted'),
        interpretation=INTERPRETATION)


def _hidden(output):
    import torch
    hidden = output[0] if isinstance(output, tuple) else output
    if not isinstance(hidden, torch.Tensor) or hidden.ndim != 3 or hidden.shape[0] != 1:
        raise ValueError('Unsupported decoder output shape for single-trajectory patching.')
    return hidden


def _capture(adapter, prefix, layer_indices, check):
    import torch
    _, layers = capture_sites(adapter)
    captured, handles = {}, []
    ids = torch.tensor([prefix], device=adapter.device)
    def capture_at(layer):
        def hook(module, args, output):
            check()
            hidden = _hidden(output)
            if hidden.shape[1] != len(prefix):
                raise ValueError('Capture token alignment changed.')
            row = hidden[0, -1].detach().clone()
            if not torch.isfinite(row).all():
                raise ValueError('Non-finite donor or identity activations.')
            captured[layer] = row
        return hook
    try:
        for layer in layer_indices:
            handles.append(layers[layer].register_forward_hook(capture_at(layer)))
        kw = {'logits_to_keep':1} if 'logits_to_keep' in inspect.signature(adapter.model.forward).parameters or getattr(adapter, 'is_muse', False) else {}
        check()
        with torch.inference_mode():
            adapter.model(ids, attention_mask=torch.ones_like(ids), use_cache=False, **kw)
        check()
        if set(captured) != set(layer_indices):
            raise ValueError('Not all selected decoder layers executed.')
    finally:
        for handle in handles:
            handle.remove()
    return captured


@contextmanager
def _patch(adapter, prefix, values, layer_indices, check):
    """Patch exactly the first prefill; fail on uncached or ambiguous later input."""
    import torch
    _, layers = capture_sites(adapter)
    handles, calls = [], {layer:0 for layer in layer_indices}
    def at(layer):
        def hook(module, args, output):
            check()
            hidden = _hidden(output)
            first = calls[layer] == 0
            calls[layer] += 1
            if first and hidden.shape[1] != len(prefix):
                raise ValueError('Expected one exact unpadded prefill for activation patching.')
            if not first:
                if hidden.shape[1] != 1:
                    raise ValueError('Generation is not using the expected KV cache; patching stopped.')
                return None
            if values is None:
                return None
            row = values[layer].to(device=hidden.device, dtype=hidden.dtype)
            if row.shape != hidden[0, -1].shape or not torch.isfinite(row).all():
                raise ValueError('Donor activation dimensions or values do not match the recipient.')
            changed = hidden.clone()
            changed[0, -1] = row
            return (changed, *output[1:]) if isinstance(output, tuple) else changed
        return hook
    try:
        for layer in layer_indices:
            handles.append(layers[layer].register_forward_hook(at(layer)))
        yield calls
        if any(v == 0 for v in calls.values()):
            raise ValueError('The generator did not execute every selected decoder layer.')
    finally:
        for handle in handles:
            handle.remove()


def summarize(observations):
    counts = {arm:{} for arm in ARMS}
    for item in observations:
        arm = counts[item['arm']]
        arm[item['label']] = arm.get(item['label'], 0) + 1
    comparisons = []
    for draw in sorted({o['draw'] for o in observations}):
        pair = {o['arm']:o for o in observations if o['draw'] == draw}
        if all(a in pair for a in ('baseline', 'identity')):
            comparisons.append(pair['baseline']['continuation_ids'] == pair['identity']['continuation_ids'])
    return dict(counts=counts, identity_control=dict(draws_compared=len(comparisons),
        exact_matches=sum(comparisons), passed=all(comparisons) if comparisons else None),
        capped=sum(o['stop_reason']=='length' for o in observations),
        note='All counts include Other/unresolved. No significance test; same seeds do not guarantee identical numerical execution on every device.')


def run(adapter, plan, check, progress, save):
    if not same_model_identity(adapter.info, plan['model']):
        raise ValueError('Attach the exact planned source model and revision.')
    if not options(adapter)['available']:
        raise ValueError(options(adapter)['reason'])
    q = plan['request']
    started = time.monotonic()
    def bounded_check():
        check()
        if time.monotonic()-started >= q['max_seconds']:
            raise TimeoutError('Activation-patching time limit reached. Completed draws remain saved.')
    out = dict(copy.deepcopy(plan), status='running', created=time.time(), observations=[], activation_summary=[])
    save(out)
    caches = {}
    for arm in ('donor', 'recipient'):
        bounded_check()
        progress(f'Capturing {arm} states at the selected position', 0, plan['continuations'])
        caches[arm] = _capture(adapter, plan['prefixes'][arm]['input_ids'], q['layers'], bounded_check)
        for layer, row in caches[arm].items():
            out['activation_summary'].append(dict(arm=arm, layer=layer, dimensions=row.numel(),
                norm=float(row.float().norm().cpu()), prefix_sha256=plan['prefixes'][arm]['prefix_sha256']))
    prefix = plan['prefixes']['recipient']
    for draw in range(q['samples']):
        seed = (q['seed'] + draw) % (2**31-1)
        for arm in ARMS:
            bounded_check()
            values = None if arm == 'baseline' else caches['recipient' if arm == 'identity' else 'donor']
            progress(f'{arm.capitalize()} · draw {draw+1}/{q["samples"]}', len(out['observations']), plan['continuations'])
            with _patch(adapter, prefix['input_ids'], values, q['layers'], bounded_check) as calls:
                sampled, seconds = adapter.resample([prefix['input_ids']], n=1,
                    max_tokens=q['cont_max'], temperature=q['temperature'], seed=seed)
            if len(sampled)!=1 or len(sampled[0])!=1:
                raise ValueError('Model returned an unexpected continuation count.')
            observation = inspect_generated(adapter, prefix['response_ids'], sampled[0][0], q['cont_max'], plan['answers'], rule=plan.get('outcome_rule'))
            out['observations'].append(dict(observation, arm=arm, draw=draw, seed=seed,
                wall_seconds=seconds, prefill_patch_count=0 if arm=='baseline' else len(q['layers']),
                forward_calls={str(k):v for k,v in calls.items()}))
            out['summary'] = summarize(out['observations'])
            save(out)
            bounded_check()
    out.update(status='complete', finished=time.time())
    if out['summary']['identity_control']['passed'] is not True:
        out['control_warning'] = 'Identity control differed from baseline. Treat effects as inconclusive until numerical or hook behavior is investigated.'
    save(out)
    return out
