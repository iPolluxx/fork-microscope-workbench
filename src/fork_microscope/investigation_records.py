# generated: Codex — durable response and outcome-rule contracts for the existing workflow.
"""Data-only validation and classifier shared by preview and execution."""
import copy
import hashlib
import json
import re
import time
from fork_microscope.outcome_readout import validate_answers, completed_reply, match_answer_text


def exact_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def validate_rule(rule):
    if not isinstance(rule, dict) or rule.get('schema') != 'fork-outcome-rule-v1':
        raise ValueError('Use a versioned outcome rule.')
    allowed = {'schema', 'method', 'answers', 'marker'}
    if set(rule) - allowed or rule.get('method') not in ('text_match', 'final_marker'):
        raise ValueError('Supported classifiers are text_match and final_marker.')
    answers = validate_answers(rule.get('answers'))
    result = dict(schema='fork-outcome-rule-v1', method=rule['method'], answers=answers)
    if rule['method'] == 'final_marker':
        marker = rule.get('marker', 'Final answer:')
        if not isinstance(marker, str) or not 1 <= len(marker.strip()) <= 100 or '\n' in marker or '\r' in marker:
            raise ValueError('Provide a one-line final answer marker.')
        result['marker'] = marker.strip()
    return result


def classify(rule, text, complete=True, is_muse=False):
    rule = validate_rule(rule)
    if not isinstance(text, str) or type(complete) is not bool:
        raise ValueError('Provide text and a boolean completion status.')
    reply = completed_reply(text, is_muse) if complete else None
    evidence = reply
    matches = []
    if reply is not None:
        if rule['method'] == 'final_marker':
            marker = re.escape(rule['marker'])
            # Only explicit line-leading markers qualify. Repeated contradictory
            # markers remain ambiguous rather than silently preferring the last.
            lines = re.findall(r'^\s*' + marker + r'\s*(.*?)\s*$', reply, re.I | re.M)
            evidence = '\n'.join(lines)
        matches = match_answer_text(evidence, rule['answers'])
    return dict(label=matches[0] if len(matches) == 1 else 'Other', matched_answers=matches,
                complete=complete, reply_available=reply is not None, evidence_text=evidence,
                rule_id=exact_hash(rule), status='incomplete' if not complete else
                'matched' if len(matches) == 1 else 'ambiguous' if matches else 'unmatched')


def validate_context(context):
    if not isinstance(context, dict) or set(context) - {'name', 'question', 'input', 'outcome_rule', 'model'}:
        raise ValueError('Invalid investigation context.')
    value = copy.deepcopy(context)
    for key in ('name', 'question'):
        if not isinstance(value.get(key), str) or not 1 <= len(value[key].strip()) <= 2000:
            raise ValueError('Provide an investigation name and question.')
    inp = value.get('input')
    if not isinstance(inp, dict) or inp.get('schema') != 'fork-input-v1' or set(inp) - {'schema', 'prompt', 'mode', 'messages'}:
        raise ValueError('Use a fork-input-v1 prompt and mode.')
    if not isinstance(inp['prompt'], str) or not 1 <= len(inp['prompt'].strip()) <= 16000 or inp['mode'] not in ('chat', 'base'):
        raise ValueError('Invalid prompt or mode.')
    if 'messages' in inp:
        validate_messages(inp['messages'])
        if inp['mode'] != 'chat': raise ValueError('Conversation history requires chat mode.')
    value['outcome_rule'] = validate_rule(value.get('outcome_rule'))
    return value


def adapt_workflow(record):
    """In-memory view only: legacy immutable settings and evidence are retained."""
    out = copy.deepcopy(record)
    for name in ('responses', 'searches', 'comparisons', 'captures', 'edits', 'patches', 'operations'):
        out.setdefault(name, [])
    out.setdefault('selected_response_id', None)
    out.setdefault('conclusion', '')
    out.setdefault('context', {'name': 'Investigation ' + out['id'][:8], 'question': (out.get('config') or {}).get('base', {}).get('prompt', ''), 'provenance': 'legacy_inferred'})
    return out


def validate_response(value):
    if not isinstance(value, dict) or value.get('schema') != 'fork-response-v1' or value.get('status') != 'complete':
        raise ValueError('Only complete durable response artifacts are supported.')
    from fork_microscope.investigation_bundle import identifier
    identifier(value.get('id'))
    base = value.get('base', {})
    vocab = value.get('model', {}).get('vocab_size', 2**31)
    for key in ('prompt_ids', 'gen_ids'):
        ids = base.get(key)
        if not isinstance(ids, list) or (key == 'prompt_ids' and not ids) or any(type(x) is not int or not 0 <= x < vocab for x in ids):
            raise ValueError('Invalid response token IDs.')
    if base.get('finish_reason') not in ('stop', 'length') or not isinstance(base.get('base_text'), str):
        raise ValueError('Missing response text or completion status.')
    if value.get('source_ids_sha256') != exact_hash({k: base[k] for k in ('prompt_ids', 'gen_ids')}):
        raise ValueError('Response token checksum mismatch.')
    rule = value.get('outcome_rule')
    if rule is not None:
        validate_rule(rule)
        expected = classify(rule, value.get('raw_text', base['base_text']), base['finish_reason'] == 'stop', value.get('is_muse', False))
        if value.get('classification') != expected:
            raise ValueError('Response classification differs from its recorded rules and text.')
    return value


def make_response(model, base, config, identifier, investigation_id=None):
    rule = config.get('outcome_rule')
    if rule is None and 'answers' in config:
        rule = dict(schema='fork-outcome-rule-v1', method='text_match', answers=config['answers'])
    raw = model.tokenizer.decode(base.gen_ids, skip_special_tokens=False)
    source = dict(prompt_ids=list(base.prompt_ids), gen_ids=list(base.gen_ids),
                  base_text=model.decode(base.gen_ids), finish_reason=base.finish_reason)
    result = dict(schema='fork-response-v1', id=identifier, status='complete', created=time.time(),
                  investigation_id=investigation_id, model=copy.deepcopy(model.info), base=source,
                  raw_text=raw, is_muse=getattr(model, 'is_muse', False), config=copy.deepcopy(config),
                  outcome_rule=rule, classification=classify(rule, raw, base.finish_reason == 'stop', getattr(model, 'is_muse', False)) if rule else None,
                  source_ids_sha256=exact_hash({k: source[k] for k in ('prompt_ids', 'gen_ids')}),
                  usage={'generated_tokens': len(base.gen_ids), 'estimated_dollars': None})
    template = getattr(model.tokenizer, 'chat_template', None)
    result['prompt_provenance'] = dict(mode=config['mode'], messages=copy.deepcopy(config.get('messages', [])), chat_template_sha256=exact_hash(template) if template and config['mode']=='chat' else None)
    return validate_response(result)


def validate_messages(messages):
    if not isinstance(messages, list) or len(messages) > 100:
        raise ValueError('Conversation history must be a list with at most 100 messages.')
    for message in messages:
        if not isinstance(message, dict) or set(message) != {'role', 'content'} or message['role'] not in ('system', 'user', 'assistant') or not isinstance(message['content'], str):
            raise ValueError('History messages need a supported role and text content.')
    if sum(len(x['content']) for x in messages) > 16000:
        raise ValueError('Conversation history exceeds 16,000 characters.')
    return messages
