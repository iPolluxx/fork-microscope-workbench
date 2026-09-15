"""Metadata-only eligibility checks and stable identities for native model loading.

Preflight never loads model weights. Local identities are computed when attaching,
using file contents rather than path names or modification times.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import re


def same_model_identity(left, right):
    """Match pinned Hub assets or verified local contents, never an unverified path.

    Local safetensors/config/tokenizer snapshots can move between workers. Their
    content fingerprint is the identity; the worker directory is only a locator.
    This does not claim matching runtime dtype, sampling settings or generated
    traces: callers must check those separately when required.
    """
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    for info in (left, right):
        if not isinstance(info.get('model_id'), str) or not info['model_id'].strip():
            return False
        if not isinstance(info.get('resolved_revision'), str) or not info['resolved_revision'].strip():
            return False
    revisions = [left['resolved_revision'], right['resolved_revision']]
    local = any(info.get('source_type') == 'local' or info.get('identity_kind') == 'local_content_sha256'
                or info['resolved_revision'].startswith('local-sha256:') for info in (left, right))
    if local:
        return (all(re.fullmatch(r'local-sha256:[a-f0-9]{64}', revision) for revision in revisions)
                and all(info.get('source_type') in (None, 'local') for info in (left, right))
                and revisions[0] == revisions[1])
    return left['model_id'] == right['model_id'] and revisions[0] == revisions[1]


def validate_model_request(payload):
    if type(payload) is not dict or set(payload) != {'model_id', 'revision', 'device', 'batch_size'}:
        raise ValueError('Provide model_id, revision, device and batch_size.')
    for key in ('model_id', 'revision'):
        if not isinstance(payload[key], str) or not payload[key].strip() or len(payload[key]) > 500:
            raise ValueError('Enter a model ID or worker directory and a revision.')
    if payload['device'] not in ('auto', 'cpu', 'cuda'):
        raise ValueError('Choose auto, CPU or CUDA.')
    if type(payload['batch_size']) is not int or not 1 <= payload['batch_size'] <= 128:
        raise ValueError('Concurrent continuations must be an integer from 1 to 128.')


def model_location(model_id):
    """Return source and canonical location, rejecting URLs and missing local paths."""
    value = model_id.strip()
    path = Path(value).expanduser()
    if path.exists() or value.startswith(('/', './', '../', '~')):
        if not path.is_dir():
            raise ValueError('The local model directory does not exist on this worker.')
        return 'local', str(path.resolve())
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?', value):
        raise ValueError('Use a Hugging Face repository ID or an existing worker directory, not an API URL.')
    return 'hub', value


def local_model_files(directory):
    root = Path(directory)
    suffixes = {'.json', '.safetensors', '.model', '.txt', '.tiktoken', '.vocab', '.merges', '.jinja', '.jinja2'}
    files = sorted(p for p in root.rglob('*') if p.is_file() and p.suffix in suffixes
                   and not any(part.startswith('.') for part in p.relative_to(root).parts))
    if not any(p.suffix == '.safetensors' for p in files):
        raise ValueError('A local native model needs safetensors weights; GGUF and pickle weight files are not supported.')
    if not (root / 'config.json').is_file():
        raise ValueError('The local model directory is missing config.json.')
    return files


def local_model_identity(directory, progress=None):
    """Hash all candidate native model/config/tokenizer files, including weight bytes."""
    root = Path(directory)
    files = local_model_files(root)
    initial = {str(p): (p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ino) for p in files}
    digest = hashlib.sha256()
    for i, path in enumerate(files):
        if progress:
            progress(f'Verifying local model identity · file {i + 1}/{len(files)}…')
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, 'big')); digest.update(relative)
        before = path.stat()
        digest.update(before.st_size.to_bytes(8, 'big'))
        with path.open('rb') as handle:
            while chunk := handle.read(8 * 1024 * 1024):
                digest.update(chunk)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError('Local model files changed during identity verification. Retry with an unchanged snapshot.')
    final = {str(p): (p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ino) for p in local_model_files(root)}
    if final != initial:
        raise ValueError('Local model files changed during identity verification. Retry with an unchanged snapshot.')
    return 'local-sha256:' + digest.hexdigest()


def native_loader(config):
    # generated: Qwen 3.5/3.6 ship a conditional-generation wrapper, even though
    # Transformers also registers their outer config in the causal-LM mapping.
    # Passing that nested config to the flat text-only constructor is not valid.
    from transformers import AutoModelForCausalLM, AutoModelForImageTextToText
    if getattr(config, 'model_type', None) in ('muse_glimmer', 'qwen3_5'):
        return 'AutoModelForImageTextToText' if type(config) in AutoModelForImageTextToText._model_mapping else None
    return 'AutoModelForCausalLM' if type(config) in AutoModelForCausalLM._model_mapping else None


def context_limit(config):
    text = getattr(config, 'text_config', config)
    for key in ('max_position_embeddings', 'n_positions', 'max_sequence_length', 'seq_length'):
        value = getattr(text, key, None)
        if type(value) is int and value > 0:
            return value
    return None


def preflight_model(payload, runtime):
    """Inspect architecture, tokenizer and metadata; return advisory load eligibility."""
    validate_model_request(payload)
    from transformers import AutoConfig, AutoTokenizer, AutoProcessor
    source, model_id = model_location(payload['model_id'])
    device = payload['device']
    if device == 'auto':
        device = 'cuda' if runtime.get('cuda_available') else 'cpu'
    report = dict(model_id=model_id, requested_revision=payload['revision'], source_type=source,
        resolved_revision=None, device=device, architecture=None, loader=None, chat_template=None,
        context_limit=None, parameters=None, blockers=[], warnings=[], can_load=False,
        verification='metadata_only', capabilities=dict(exact_tokens=False, next_token_logits=False,
        forced_prefix=False, chat=False), memory=dict(weights_gb=None, minimum_estimate_gb=None,
        available_gb=runtime.get('gpu_free_gb', runtime.get('gpu_memory_gb')) if device == 'cuda'
            else runtime.get('system_available_memory_gb', runtime.get('system_memory_gb')),
        assumptions='Full weights at float32 on CPU or float16/bfloat16 on CUDA. The estimate excludes KV cache, context, batch expansion and allocator overhead.'))
    if device == 'cuda' and not runtime.get('cuda_available'):
        report['blockers'].append('This worker has no CUDA GPU. Select CPU or open the app on a GPU worker.')
    revision = None if source == 'local' else payload['revision']
    local_weights = None
    if source == 'local':
        try:
            files = local_model_files(model_id)
            local_weights = sum(p.stat().st_size for p in files if p.suffix == '.safetensors')
        except ValueError as exc:
            report['blockers'].append(str(exc))
            return report
        report['warnings'].append('Local file contents are fingerprinted during attachment. Keep this directory unchanged for replay and comparison.')
    try:
        config = AutoConfig.from_pretrained(model_id, revision=revision, trust_remote_code=False)
    except Exception:
        report['blockers'].append('Could not read a native model configuration. Check the repository/directory, revision and worker-side Hugging Face access. Custom remote code is disabled.')
        return report
    resolved = getattr(config, '_commit_hash', None) if source == 'hub' else None
    report['resolved_revision'] = resolved
    if source == 'hub' and not resolved:
        report['blockers'].append('The Hub revision could not be pinned to an immutable commit.')
    report['architecture'] = getattr(config, 'model_type', None)
    report['loader'] = native_loader(config)
    # generated: model wrapper support does not enable image/video input in this app.
    if report['architecture'] == 'qwen3_5':
        report['warnings'].append('Qwen uses its native conditional-generation wrapper with text input only. Image and video inputs are not supported by this worker.')
    report['context_limit'] = context_limit(config)
    if not report['loader']:
        report['blockers'].append('This architecture has no supported native text-generation loader in the installed Transformers version.')
    if getattr(config, 'quantization_config', None):
        report['blockers'].append('Pre-quantized models are not supported by this full-weight adapter. Select a native safetensors checkpoint.')
    if not report['context_limit']:
        report['blockers'].append('The model configuration does not declare a supported context limit.')
    try:
        kw = dict(revision=resolved if source == 'hub' else None, trust_remote_code=False)
        tokenizer = (AutoProcessor.from_pretrained(model_id, **kw).tokenizer
                     if report['architecture'] == 'muse_glimmer'
                     else AutoTokenizer.from_pretrained(model_id, **kw))
        report['chat_template'] = bool(tokenizer.chat_template)
        if tokenizer.eos_token_id is None:
            report['warnings'].append('The tokenizer has no EOS token; attachment will require a model generation EOS marker.')
        if tokenizer.eos_token_id is None and tokenizer.pad_token_id is None:
            report['blockers'].append('The tokenizer requires an EOS or padding token for batched generation.')
        if not report['chat_template']:
            report['warnings'].append('No chat template is present. Use Base / completion format in the Question step.')
    except Exception:
        report['blockers'].append('Could not load the native tokenizer. Check worker-side model access and tokenizer support.')
    if source == 'hub':
        try:
            from huggingface_hub import HfApi
            info = HfApi().model_info(model_id, revision=resolved or revision, timeout=12, expand=['safetensors'])
            parameters = getattr(getattr(info, 'safetensors', None), 'total', None)
            if type(parameters) is int and parameters > 0:
                report['parameters'] = parameters
                report['memory']['weights_gb'] = round(parameters * (4 if device == 'cpu' else 2) / 1024**3, 2)
        except Exception:
            pass  # Hub metadata can omit parameter totals; this does not prove incompatibility.
    elif local_weights is not None:
        report['memory']['stored_weights_gb'] = round(local_weights / 1024**3, 2)
        report['warnings'].append('Stored safetensors size is not a RAM/VRAM estimate; loading may change the weight dtype.')
    weights = report['memory']['weights_gb']
    if weights is not None:
        report['memory']['minimum_estimate_gb'] = weights
        available = report['memory']['available_gb']
        if isinstance(available, (float, int)) and weights > available:
            report['blockers'].append(f'Weights alone require approximately {weights:.1f} GiB, above the reported {available:.1f} GiB available memory. Use a smaller model or larger worker.')
        report['warnings'].append('Leave additional memory for prompt processing and continuation KV caches; weight fit alone does not guarantee the run fits.')
    else:
        report['warnings'].append('A reliable loaded-weight memory estimate is unavailable. Verify model size against this worker before attaching.')
    if device == 'cpu':
        report['warnings'].append('CPU execution is supported for eligible smaller models, but long sampling runs can be slow.')
    report['warnings'].append('Native loader eligibility is not a completed model validation. First run a small prompt and inspect the answer parsing.')
    eligible = bool(report['loader'])
    report['capabilities'] = dict(exact_tokens=eligible, next_token_logits=eligible,
        forced_prefix=eligible, chat=bool(report['chat_template']))
    report['can_load'] = not report['blockers']
    return report
