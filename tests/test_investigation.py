# generated: Codex — exact-prefix and non-mutating capture checks, no downloaded weights.
from types import SimpleNamespace
import copy
import json
import pytest
import torch
from transformers import LlamaConfig, LlamaForCausalLM
from fork_microscope.investigation import build_plan, capture, run_edit, digest


class Tokenizer:
    def __call__(self, text, **kwargs):
        return {'input_ids': [int(x) for x in text.split()]}

    def decode(self, ids, **kwargs):
        return ' '.join(map(str, ids))


def fixture():
    torch.manual_seed(3)
    config = LlamaConfig(vocab_size=32, hidden_size=16, intermediate_size=32,
                        num_hidden_layers=2, num_attention_heads=2, num_key_value_heads=2)
    config._attn_implementation = 'eager'
    model = LlamaForCausalLM(config).eval()
    adapter = SimpleNamespace(model=model, tokenizer=Tokenizer(), device='cpu', eos_ids=[31], is_muse=False,
        info={'model_id':'local-test', 'resolved_revision':'pinned', 'vocab_size':32, 'context_limit':64})
    adapter.decode = adapter.tokenizer.decode
    base = {'prompt_ids':[1,2], 'gen_ids':[3,4,5,6], 'base_text':'3 4 5 6'}
    result = {'id':'a'*32, 'model':dict(adapter.info), 'passes':[{'id':'p'}],
              'base_config':{'answers':['7','8']}, 'records':{'p':{'base':base}}}
    common = {'source_run_id':result['id'], 'source_pass_id':'p'}
    return adapter, base, result, common


def test_exact_edit_prefixes_deletion_and_source_unchanged():
    a, b, r, q = fixture(); before=copy.deepcopy(r)
    q.update(kind='edit',start=1,end=3,replacement='9 10',samples=2,cont_max=8,temperature=1,seed=4)
    p=build_plan(a,r,q)
    assert p['arms']=={'control':[3,4,5],'edit':[3,9,10]}
    assert p['source_base']['prompt_ids']==[1,2]
    assert p['source_base']['gen_ids']==[3,4,5,6]
    assert p['max_new_tokens']==32 and r==before
    q.update(start=0,replacement='');p=build_plan(a,r,q)
    assert p['arms']['edit']==[] and p['arms']['control']==[3,4,5]
    q['cont_max']=64
    with pytest.raises(ValueError,match='context'):build_plan(a,r,q)
    q['cont_max']=8;a.info['resolved_revision']='wrong'
    with pytest.raises(ValueError,match='exact'):build_plan(a,r,q)


def test_fresh_control_and_edit_draws_record_seed_completion_and_partial_data():
    a,b,r,q=fixture();q.update(kind='edit',start=1,end=2,replacement='9',samples=2,cont_max=3,temperature=1,seed=4)
    p=build_plan(a,r,q);calls=[];saves=[]
    def resample(prefixes,**kw):
        calls.append((prefixes,kw));return ([[[7] if len(calls)<4 else [8]*3]],.1)
    a.resample=resample
    out=run_edit(a,b,['7','8'],p,lambda:None,lambda *a:None,lambda v:saves.append(copy.deepcopy(v)))
    assert [c[0][0] for c in calls]==[[1,2,3,4],[1,2,3,9]]*2
    assert [o['seed'] for o in out['observations']]==[4,5,6,7]
    assert [o['label'] for o in out['observations']]==['7','7','7','Other']
    assert out['observations'][-1]['stop_reason']=='length'
    assert saves[1]['status']=='running' and len(saves[1]['observations'])==1
    assert out['status']=='complete'


def test_capture_matches_hidden_states_at_exact_prefix_and_preserves_logits():
    a,b,r,q=fixture();q.update(kind='activation',positions=[0,2,4],layers=[0,1])
    p=build_plan(a,r,q);full=torch.tensor([b['prompt_ids']+b['gen_ids']])
    with torch.no_grad():before=a.model(full).logits.clone()
    out=capture(a,b,p,lambda:None,lambda *a:None,lambda v:None)
    assert all(not layer._forward_hooks for layer in a.model.model.layers)
    for row in out['captures']:
        ids=b['prompt_ids']+b['gen_ids'][:row['checkpoint']]
        assert row['absolute_position']==len(ids)-1 and row['prefix_sha256']==digest(ids)
        with torch.no_grad():states=a.model(torch.tensor([ids]),output_hidden_states=True).hidden_states
        actual=torch.tensor(row['vector'])
        if row['layer']==0: expected=states[1][0,-1]
        else: actual=a.model.model.norm(actual);expected=states[-1][0,-1]
        torch.testing.assert_close(actual,expected)
    with torch.no_grad():torch.testing.assert_close(a.model(full).logits,before,rtol=0,atol=0)
    # Transformers installs its own persistent output-recording hooks when the
    # independent hidden_states reference is requested above.


def test_hooks_removed_on_forward_failure_and_cancel_saves_first_prefix():
    a,b,r,q=fixture();q.update(kind='activation',positions=[0,2],layers=[0])
    p=build_plan(a,r,q)
    def fail(*args):raise RuntimeError('injected failure')
    handle=a.model.model.layers[1].register_forward_hook(fail)
    with pytest.raises(RuntimeError,match='injected'):capture(a,b,p,lambda:None,lambda *a:None,lambda v:None)
    assert not a.model.model.layers[0]._forward_hooks
    handle.remove()
    saves=[]
    def check():
        if saves and saves[-1].get('captures'):raise RuntimeError('cancel')
    with pytest.raises(RuntimeError,match='cancel'):
        capture(a,b,p,check,lambda *a:None,lambda v:saves.append(copy.deepcopy(v)))
    assert len(saves[-1]['captures'])==1
    assert all(not layer._forward_hooks for layer in a.model.model.layers)


def test_capture_rejects_unsupported_architecture_bad_layers_and_changed_text():
    a,b,r,q=fixture();q.update(kind='activation',positions=[0],layers=[2])
    with pytest.raises(ValueError,match='layers'):build_plan(a,r,q)
    q['layers']=[0];b['base_text']='wrong'
    with pytest.raises(ValueError,match='tokenizer'):build_plan(a,r,q)
    b['base_text']='3 4 5 6';a.model.config.model_type='unknown'
    with pytest.raises(ValueError,match='not supported'):build_plan(a,r,q)


def test_service_serialization_failure_status_and_artifact_access(tmp_path,monkeypatch):
    from fork_microscope import live_service
    from fork_microscope.live_service import LiveService
    a,b,r,q=fixture();q.update(kind='edit',start=1,end=2,replacement='9',samples=2,cont_max=3,temperature=1,seed=4)
    monkeypatch.setattr(live_service,'RUNS',tmp_path/'runs')
    s=LiveService(workspace_root=tmp_path/'workspace');s.model=a
    monkeypatch.setattr(s,'result',lambda *args,**kw:r)
    p=s.investigation_plan(q)
    s.job={'id':'b'*32,'status':'running'}
    with pytest.raises(ValueError,match='current'):s.investigation_plan(q)
    def fail(*args,**kw):raise RuntimeError('draw failure')
    a.resample=fail;s._execute('investigate',p)
    assert s.job['status']=='error'
    saved=s.investigation('b'*32)
    assert saved['status']=='error' and saved['error']=='draw failure'
    assert s.investigations()[0]['id']=='b'*32
    assert s.base is None
    with pytest.raises(ValueError,match='Invalid'):s.investigation('../secret')


def test_investigation_http_routes_and_assets_require_correct_access(tmp_path,monkeypatch):
    from http.server import ThreadingHTTPServer
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    import threading
    from fork_microscope import live_service, microscope_server
    from fork_microscope.live_service import LiveService
    from fork_microscope.worker_connection import WorkerAccess
    a,b,r,q=fixture();q.update(kind='activation',positions=[0],layers=[0])
    monkeypatch.setattr(live_service,'RUNS',tmp_path/'runs')
    s=LiveService(workspace_root=tmp_path/'workspace');s.model=a
    monkeypatch.setattr(s,'result',lambda *args,**kw:r)
    monkeypatch.setattr(microscope_server,'LIVE',s)
    server=ThreadingHTTPServer(('127.0.0.1',0),microscope_server.Handler)
    server.worker_access=WorkerAccess(token='x'*32)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}'
    headers={'Authorization':'Bearer '+'x'*32,'Content-Type':'application/json'}
    try:
        for asset in ('investigation-panel.mjs','investigation-panel.css'):
            with urlopen(url+'/'+asset) as response:assert response.status==200
        for route in ('investigations','investigation?id='+'b'*32):
            with pytest.raises(HTTPError) as exc:urlopen(url+'/api/live/'+route)
            assert exc.value.code==401
        body=json.dumps(q).encode()
        with pytest.raises(HTTPError) as exc:
            urlopen(Request(url+'/api/live/investigation-plan',data=body,headers={'Content-Type':'application/json'}))
        assert exc.value.code==401
        with urlopen(Request(url+'/api/live/investigation-plan',data=body,headers=headers)) as response:
            assert json.load(response)['vectors']==1
        with urlopen(Request(url+'/api/live/investigations',headers=headers)) as response:
            assert json.load(response)==[]
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
