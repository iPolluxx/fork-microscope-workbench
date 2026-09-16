"""Model-specific answer extraction without confusing Muse reasoning with its reply."""
import re
import unicodedata
from forking_paths.answers import parse_mmlu_answer
from forking_paths.run import extract_answers_for_branches as upstream_extract



def normalize_answer(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def validate_answers(answers):
    if type(answers) is not list or not 1 <= len(answers) <= 32:
        raise ValueError('Enter 1–32 expected answers, one per line.')
    if any(not isinstance(x, str) or not x.strip() or len(x) > 200 or '\n' in x or '\r' in x for x in answers):
        raise ValueError('Each answer must be nonempty, on one line, and at most 200 characters.')
    normalized = [normalize_answer(x) for x in answers]
    if len(set(normalized)) != len(normalized) or 'other' in normalized:
        raise ValueError('Answers must be unique ignoring case and whitespace. Other is reserved for unresolved outcomes.')
    return [x.strip() for x in answers]


def match_answer_text(reply, answers):
    """Literal phrase matches, normalized case/whitespace, bounded by word edges.

    Multiple distinct matches remain unresolved; this is not a semantic judge.
    """
    text = normalize_answer(reply)
    matches = []
    for answer in answers:
        term = normalize_answer(answer)
        pattern = (r'(?<!\w)' if re.match(r'\w', term[0]) else '') + re.escape(term) + (r'(?!\w)' if re.match(r'\w', term[-1]) else '')
        if re.search(pattern, text):
            matches.append(answer)
    return matches


def completed_reply(raw, is_muse):
    if is_muse:
        marker = 'to=user<|message|>'
        if marker not in raw: return None
        raw = raw.rsplit(marker, 1)[1]
    else:
        # Common explicit reasoning wrapper. Untagged reasoning cannot be
        # separated generically; its mentions can affect text matching.
        if '<think>' in raw and '</think>' not in raw: return None
        if '</think>' in raw: raw = raw.rsplit('</think>', 1)[1]
    for marker in ('<|eot|>', '<|end_of_text|>', '<|im_end|>', '</s>'):
        raw = raw.split(marker, 1)[0]
    return raw


def inspect_base(model, base, answers=None, rule=None):
    """Preview the same completed-reply matcher used for collected outcomes."""
    complete = base.finish_reason == 'stop'
    raw = model.tokenizer.decode(base.gen_ids, skip_special_tokens=False)
    if rule is not None:
        from fork_microscope.investigation_records import classify
        return classify(rule, raw, complete, getattr(model, 'is_muse', False))
    reply = completed_reply(raw, getattr(model, 'is_muse', False)) if complete else None
    matches = match_answer_text(reply, answers) if reply is not None and answers is not None else []
    label = (matches[0] if len(matches) == 1 else None) if answers is not None else (
        parse_mmlu_answer(reply) if reply is not None else None)
    return dict(complete=complete, label=label or 'Other', matched_answers=matches,
        reply_available=reply is not None,
        status='incomplete' if not complete else 'matched' if label else 'ambiguous' if matches else 'unmatched')


def muse_answer(raw):
    marker="to=user<|message|>"
    if marker not in raw:
        return None
    reply=raw.rsplit(marker,1)[1]
    endings=[reply.index(x) for x in ("<|eot|>","<|end_of_text|>") if x in reply]
    if not endings: return None
    return parse_mmlu_answer(reply[:min(endings)])


def extract_answers_for_branches(model, base, branches, continuations, cfg, suffix, diag_sample=0):
    if not getattr(model,"is_muse",False):
        return upstream_extract(model,base,branches,continuations,cfg,suffix,diag_sample=diag_sample)
    answers=[]; total=resolved=0
    for branch,draws in zip(branches,continuations):
        labels=[]
        for cont in draws:
            ids=base.gen_ids[:branch.idx]+[branch.tok_id]+cont
            # The upstream sampler removes its terminal EOS. A short continuation
            # stopped by EOS (or an EOS branch) has completed the turn.
            complete=branch.tok_id in model.eos_ids or len(cont)<cfg.cont_max_tokens
            raw=model.tokenizer.decode(ids,skip_special_tokens=False)
            if complete and "<|eot|>" not in raw:
                raw+="<|eot|>"
            label=muse_answer(raw)
            labels.append(label or "Other");total+=1;resolved+=label is not None
        answers.append(labels)
    return answers,dict(n_continuations=total,n_regex_resolved=resolved,
        regex_coverage=resolved/total if total else None,n_logit_fallback=0,
        n_other=total-resolved,regex_vs_logit_agreement=None,
        extractor="Muse completed to=user channel; unfinished/unparseable -> Other")


def inspect_continuation(model, base, branch, cont, cap, answers=None, rule=None):
    """Strict outcome readout: a capped generation is never a final answer.

    Upstream strips EOS; len(cont)<cap (or a forced EOS) establishes stopping.
    The exact stripped EOS token cannot be recovered and is not fabricated.
    """
    complete = branch.tok_id in model.eos_ids or len(cont)<cap
    ids=base.gen_ids[:branch.idx]+[branch.tok_id]+cont
    raw=model.tokenizer.decode(ids,skip_special_tokens=False)
    text=model.tokenizer.decode(cont,skip_special_tokens=False)
    is_muse=getattr(model,'is_muse',False)
    channel=('user' if 'to=user<|message|>' in raw else 'reasoning') if is_muse else 'not_applicable'
    label=None; source='incomplete'; matches=[]
    reply=completed_reply(raw, is_muse) if complete else None
    if complete:
        if answers is not None:
            matches = match_answer_text(reply, answers) if reply is not None else []
            label = matches[0] if len(matches) == 1 else None
            source = 'answer_text' if label else ('ambiguous' if matches else 'unparsed')
        elif is_muse:
            # An EOS removed by the upstream sampler still terminates the reply.
            label=muse_answer(raw+'<|eot|>')
            source='muse_completed_user' if label else 'unparsed'
        else:
            reply=raw  # Preserve the exact legacy regex input for inspection.
            label=parse_mmlu_answer(raw)
            source='completed_regex' if label else 'unparsed'
    if rule is not None:
        from fork_microscope.investigation_records import classify
        readout = classify(rule, raw, complete, is_muse)
        label = readout['label']; matches = readout['matched_answers']; source = readout['status']
    return dict(label=label or 'Other',label_source=source,channel_reached=channel,matched_answers=matches,reply_text=reply,
        stop_reason='eos' if complete else 'length',
        stop_reason_evidence='forced_eos' if branch.tok_id in model.eos_ids else 'inferred_from_stripped_length',
        generated_tokens=len(cont),continuation_text=text,full_response_text=raw)
