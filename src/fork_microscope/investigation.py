# generated: Codex — checkpoint investigations, separate from sampled fork curves.
"""Exact-prefix text controls and bounded read-only activation capture."""
import hashlib
import inspect
import json
import math
import time

from fork_microscope.model_preflight import same_model_identity
from fork_microscope.outcome_readout import completed_reply, match_answer_text, parse_mmlu_answer


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def whole(value, name, lo, hi):
    if type(value) is not int or not lo <= value <= hi:
        raise ValueError(f'{name} must be an integer from {lo} to {hi}.')
    return value


def source(adapter, result, request):
    if not same_model_identity(adapter.info, result['model']):
        raise ValueError('Attach the exact source model and resolved revision.')
    record = result.get('records', {}).get(request['source_pass_id'])
    if record is None or request['source_pass_id'] not in [p['id'] for p in result['passes']]:
        raise ValueError('Choose a saved source pass.')
    base = record['base']
    if request.get('source_selection'):
        choice = request['source_selection']
        if not isinstance(choice, dict) or set(choice) != {'schema','type','checkpoint','draw_index'} or choice['schema'] != 'fork-trajectory-v1' or choice['type'] != 'draw':
            raise ValueError('Invalid continuation trajectory selector.')
        if any(type(choice[k]) is not int or choice[k] < 0 for k in ('checkpoint','draw_index')): raise ValueError('Invalid continuation coordinates.')
        matches = [base['gen_ids'][:b['t']] + [b['tok_id']] + b['continuation_ids'][i]
            for b in record['branches'] if b['t'] == choice['checkpoint']
            for i, draw in enumerate(b.get('draw_indices', [])) if draw == choice['draw_index']]
        if len(matches) != 1: raise ValueError('Selected continuation is missing or ambiguous.')
        base = dict(base, gen_ids=matches[0], base_text=adapter.decode(matches[0]))
    for ids in (base['prompt_ids'], base['gen_ids']):
        if not ids or any(type(i) is not int or not 0 <= i < adapter.info['vocab_size'] for i in ids):
            raise ValueError('Saved token IDs are empty or outside the model vocabulary.')
    if adapter.decode(base['gen_ids']) != base['base_text']:
        raise ValueError('Saved response IDs do not match this tokenizer.')
    return base


def capture_sites(adapter):
    """Explicit decoder-block mappings; no arbitrary client-supplied module paths."""
    kind = adapter.model.config.model_type
    paths = {'muse_glimmer': 'model.language_model.layers',
             'llama': 'model.layers', 'mistral': 'model.layers',
             'qwen2': 'model.layers', 'gemma2': 'model.layers'}
    path = paths.get(kind)
    if path is None:
        raise ValueError('Activation capture is not supported for this architecture yet.')
    layers = adapter.model
    for name in path.split('.'):
        layers = getattr(layers, name, None)
        if layers is None:
            raise ValueError('The loaded model does not expose the expected decoder blocks.')
    return path, layers


def build_plan(adapter, result, request):
    common = {'source_run_id', 'source_pass_id', 'kind'}
    if isinstance(request, dict) and 'source_selection' in request: common.add('source_selection')
    extra = {'edit': {'start', 'end', 'replacement', 'samples', 'cont_max', 'temperature', 'seed'},
             'activation': {'positions', 'layers'}}
    if type(request) is not dict or request.get('kind') not in extra or set(request) != common | extra[request['kind']]:
        raise ValueError('Invalid investigation fields.')
    if request['source_run_id'] != result['id']:
        raise ValueError('Source run mismatch.')
    base = source(adapter, result, request)
    plan = dict(schema='fork-investigation-v1', request=dict(request), model=dict(adapter.info),
                outcome_rule=result.get('base_config', {}).get('outcome_rule'),
                source_base={'prompt_ids':list(base['prompt_ids']), 'gen_ids':list(base['gen_ids']), 'base_text':base['base_text']},
                generation_defaults=adapter.model.generation_config.to_dict(),
                attention_implementation=getattr(adapter.model.config, '_attn_implementation', None),
                source_ids_sha256=digest({'prompt_ids': base['prompt_ids'], 'gen_ids': base['gen_ids']}),
                selected_after_inspecting_source=True)
    if request['kind'] == 'edit':
        start = whole(request['start'], 'Start', 0, len(base['gen_ids']) - 1)
        end = whole(request['end'], 'End (exclusive)', start + 1, len(base['gen_ids']))
        whole(request['samples'], 'Samples per arm', 2, 128)
        whole(request['cont_max'], 'Continuation cap', 1, 4096)
        whole(request['seed'], 'Seed', 0, 2**31 - 1)
        temp = request['temperature']
        if type(temp) not in (int, float) or not math.isfinite(temp) or not .05 <= temp <= 2:
            raise ValueError('Temperature must be from 0.05 to 2.')
        if not isinstance(request['replacement'], str) or len(request['replacement']) > 16000:
            raise ValueError('Replacement must be text of at most 16,000 characters. Empty text deletes the span.')
        replacement = list(adapter.tokenizer(request['replacement'], add_special_tokens=False)['input_ids'])
        arms = {'control': base['gen_ids'][:end], 'edit': base['gen_ids'][:start] + replacement}
        for prefix in arms.values():
            if any(i in adapter.eos_ids for i in prefix):
                raise ValueError('Select a span before the response termination token.')
            if len(base['prompt_ids']) + len(prefix) + request['cont_max'] > adapter.info['context_limit']:
                raise ValueError('The edited or control prefix plus continuation cap exceeds the context limit.')
        plan.update(replacement_ids=replacement, arms=arms,
                    prefix_previews={k: adapter.tokenizer.decode(v, skip_special_tokens=False) for k, v in arms.items()},
                    original_span=adapter.tokenizer.decode(base['gen_ids'][start:end], skip_special_tokens=False),
                    continuations=2 * request['samples'], max_new_tokens=2 * request['samples'] * request['cont_max'],
                    sampling=dict(temperature=temp, top_p=1, top_k=0, batch_size=1,
                                  branch_filter=False, suffix_discarded=True),
                    interpretation='Fresh control and edited prefixes; full-vocabulary sampling. Not directly comparable to the retained-branch source curve. Text matching is not correctness. The edit may change token count and positions.')
    else:
        path, layers = capture_sites(adapter)
        for key, cap, hi in [('positions', 16, len(base['gen_ids'])), ('layers', 4, len(layers) - 1)]:
            values = request[key]
            if type(values) is not list or not 1 <= len(values) <= cap:
                raise ValueError(f'Choose 1–{cap} {key}.')
            for v in values:
                whole(v, key, 0, hi)
            if len(set(values)) != len(values):
                raise ValueError(f'Duplicate {key} are not allowed.')
        if len(base['prompt_ids']) + max(request['positions']) > adapter.info['context_limit']:
            raise ValueError('Capture prefix exceeds the context limit.')
        plan.update(module_path=path, site='decoder_block_output_before_final_norm',
                    position_semantics='Last input token of prompt_ids + base_ids[:checkpoint]; checkpoint 0 is the final prompt token.',
                    vectors=len(request['positions']) * len(request['layers']),
                    interpretation='Read-only internal vectors, not thoughts, semantic labels, or causal explanations. Adjacent states also differ because their input tokens differ. Architecture mappings require validation on the actual model.')
    return plan


def inspect_generated(adapter, response_prefix, continuation, cap, answers, rule=None):
    raw = adapter.tokenizer.decode(response_prefix + continuation, skip_special_tokens=False)
    complete = len(continuation) < cap  # upstream strips EOS; exact EOS-at-cap is conservatively unresolved
    reply = completed_reply(raw, getattr(adapter, 'is_muse', False)) if complete else None
    matches = match_answer_text(reply, answers) if reply is not None and answers is not None else []
    label = (matches[0] if len(matches) == 1 else None) if answers is not None else (parse_mmlu_answer(reply) if reply is not None else None)
    if rule is not None:
        from fork_microscope.investigation_records import classify
        classification = classify(rule, raw, complete, getattr(adapter, 'is_muse', False))
        label=classification['label'];matches=classification['matched_answers']
    return dict(label=label or 'Other', matched_answers=matches, reply_text=reply,
                stop_reason='eos' if complete else 'length', stop_reason_evidence='inferred_from_stripped_length',
                continuation_ids=continuation, continuation_text=adapter.tokenizer.decode(continuation, skip_special_tokens=False),
                full_response_text=raw)


def run_edit(adapter, base, answers, plan, check, progress, save):
    q = plan['request']
    out = dict(plan, status='running', observations=[], created=time.time())
    save(out)
    # Interleave arms. Each draw has a distinct recorded seed; no claim of paired randomness.
    for draw in range(q['samples']):
        for arm_index, arm in enumerate(('control', 'edit')):
            check()
            seed = (q['seed'] + 2 * draw + arm_index) % (2**31 - 1)
            prefix = base['prompt_ids'] + plan['arms'][arm]
            progress(f'{arm.capitalize()} · draw {draw + 1}/{q["samples"]}', len(out['observations']), plan['continuations'])
            sampled, seconds = adapter.resample([prefix], n=1, max_tokens=q['cont_max'], temperature=q['temperature'], seed=seed)
            if len(sampled) != 1 or len(sampled[0]) != 1:
                raise ValueError('Model returned an unexpected continuation count.')
            observation = inspect_generated(adapter, plan['arms'][arm], sampled[0][0], q['cont_max'], answers, rule=plan.get('outcome_rule'))
            out['observations'].append(dict(observation, arm=arm, draw=draw, seed=seed, wall_seconds=seconds))
            save(out)
    out.update(status='complete', finished=time.time())
    save(out)
    return out


def capture(adapter, base, plan, check, progress, save):
    import torch
    path, layers = capture_sites(adapter)
    out = dict(plan, status='running', captures=[], created=time.time(), storage_dtype='float32')
    save(out)
    for checkpoint in plan['request']['positions']:
        check()
        prefix = base['prompt_ids'] + base['gen_ids'][:checkpoint]
        ids = torch.tensor([prefix], device=adapter.device)
        captured, handles = {}, []

        def hook_for(layer):
            def hook(module, args, output):
                hidden = output[0] if isinstance(output, tuple) else output
                if not isinstance(hidden, torch.Tensor) or hidden.ndim != 3 or hidden.shape[:2] != ids.shape:
                    raise ValueError('Unexpected decoder output shape; capture stopped.')
                row = hidden[0, -1].detach().float().cpu().clone()
                if not torch.isfinite(row).all():
                    raise ValueError('Non-finite activation values.')
                captured[layer] = row
                # Returning None leaves the forward pass unchanged.
            return hook

        try:
            for layer in plan['request']['layers']:
                handles.append(layers[layer].register_forward_hook(hook_for(layer)))
            kw = {'logits_to_keep': 1} if 'logits_to_keep' in inspect.signature(adapter.model.forward).parameters or getattr(adapter, 'is_muse', False) else {}
            with torch.inference_mode():
                adapter.model(ids, attention_mask=torch.ones_like(ids), use_cache=False, **kw)
            if set(captured) != set(plan['request']['layers']):
                raise ValueError('Some requested layers did not execute.')
        finally:
            for handle in handles:
                handle.remove()
        for layer, row in captured.items():
            out['captures'].append(dict(checkpoint=checkpoint, layer=layer, module=f'{path}.{layer}',
                absolute_position=len(prefix)-1, prefix_ids=prefix, prefix_sha256=digest(prefix),
                vector=row.tolist(), norm=float(row.norm())))
        progress(f'Captured prefix at token {checkpoint}', len(out['captures']), plan['vectors'])
        save(out)
    out.update(status='complete', finished=time.time())
    save(out)
    return out
