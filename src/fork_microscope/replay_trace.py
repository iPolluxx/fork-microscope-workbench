"""Recompute sampling scores on exact saved IDs, without choosing a new base path."""
import torch
from transformers import LogitsProcessor, LogitsProcessorList
from forking_paths.model import BasePath
from fork_microscope.model_preflight import same_model_identity


def replay_saved_trace(adapter, saved, source_model, top_k=50, progress=None):
    if not same_model_identity(adapter.info, source_model):
        raise ValueError('Replay requires the exact source model and resolved revision.')
    prompt, generated = saved['prompt_ids'], saved['gen_ids']
    vocab = adapter.info['vocab_size']
    for ids in (prompt, generated):
        if not ids or any(type(i) is not int or not 0 <= i < vocab for i in ids):
            raise ValueError('Saved token IDs are empty or outside the model vocabulary.')
    if adapter.decode(generated) != saved['base_text']:
        raise ValueError('Saved text does not match IDs under this tokenizer.')
    if len(prompt)+len(generated) > adapter.info['context_limit']:
        raise ValueError('Saved trace exceeds context limit.')
    choices, probabilities = [], []

    class SavedTokens(LogitsProcessor):
        def __call__(self, input_ids, scores):
            t = input_ids.shape[1]-len(prompt)
            if t != len(choices):
                raise ValueError('Replay score alignment changed.')
            values, ids = torch.log_softmax(scores[0].float(),dim=-1).topk(min(top_k,vocab))
            choices.append(ids.tolist()); probabilities.append(values.tolist())
            if progress and t % 64 == 0: progress('Replaying saved tokens',t,len(generated))
            forced = torch.full_like(scores, -float('inf'))
            forced[0,generated[t]] = 0
            return forced

    with torch.no_grad():
        output = adapter.model.generate(torch.tensor([prompt],device=adapter.device),
            max_new_tokens=len(generated),do_sample=False,num_beams=1,
            pad_token_id=adapter.tokenizer.pad_token_id,eos_token_id=adapter.eos_ids,
            logits_processor=LogitsProcessorList([SavedTokens()]))
    if output[0].tolist()!=prompt+generated or len(choices)!=len(generated):
        raise ValueError('Replay did not reproduce all exact saved token IDs.')
    return BasePath(list(prompt),list(generated),choices,probabilities,saved['finish_reason'])
