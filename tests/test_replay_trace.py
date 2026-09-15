from types import SimpleNamespace
import pytest
import torch
from fork_microscope.replay_trace import replay_saved_trace
from fork_microscope.sampling import pass_plan
import json
from pathlib import Path

def adapter():
    class Model:
        def generate(self,x,**kw):
            for _ in range(kw['max_new_tokens']):
                scores=torch.tensor([[1.,2.,3.,4.]])
                forced=kw['logits_processor'](x,scores)
                x=torch.cat([x,forced.argmax(-1).reshape(1,1)],dim=1)
            return x
    return SimpleNamespace(info={'model_id':'tiny','resolved_revision':'abc','vocab_size':4,'context_limit':20},decode=lambda ids:'saved',device='cpu',model=Model(),tokenizer=SimpleNamespace(pad_token_id=0),eos_ids=[])

def test_replay_captures_unforced_scores_but_keeps_original_ids():
    a=adapter();saved={'prompt_ids':[1],'gen_ids':[0,2,1],'base_text':'saved','finish_reason':'stop'}
    b=replay_saved_trace(a,saved,a.info)
    assert b.gen_ids==[0,2,1] and b.prompt_ids==[1]
    assert b.topk_ids==[[3,2,1,0]]*3
    assert b.topk_logprobs[0][0]==pytest.approx(torch.log_softmax(torch.tensor([1.,2.,3.,4.]),0)[3].item())
    with pytest.raises(ValueError,match='revision'):replay_saved_trace(a,saved,dict(a.info,resolved_revision='other'))

def test_explicit_endpoint_grid():
    c=json.loads((Path(__file__).parents[1]/'tests/fixtures/refinement.json').read_text())
    p=c['run']['passes'][0];p.update(start=339,end=452,stride=8,positions=list(range(339,453,8))+[452])
    assert pass_plan(c['run'],1692)[0]['positions']==list(range(339,453,8))+[452]
    p['positions']=[339,347,347,452]
    with pytest.raises(ValueError,match='ordered'):pass_plan(c['run'],1692)


def test_relocated_local_snapshot_replays_by_full_content_identity():
    a = adapter()
    a.info.update(model_id='/new-worker/cache/model', resolved_revision='local-sha256:'+'a'*64,
                  source_type='local', identity_kind='local_content_sha256')
    source = dict(a.info, model_id='/original-worker/models/model')
    saved = dict(prompt_ids=[1], gen_ids=[0,2,1], base_text='saved', finish_reason='stop')
    result = replay_saved_trace(a, saved, source)
    assert result.gen_ids == saved['gen_ids']
    with pytest.raises(ValueError, match='revision'):
        replay_saved_trace(a, saved, dict(source, resolved_revision='local-sha256:'+'b'*64))


def test_local_path_without_content_identity_cannot_replay():
    a = adapter()
    a.info.update(model_id='/same/path', resolved_revision='local', source_type='local')
    saved = dict(prompt_ids=[1], gen_ids=[0,2,1], base_text='saved', finish_reason='stop')
    with pytest.raises(ValueError, match='revision'):
        replay_saved_trace(a, saved, dict(a.info))
