"""Offline direct-adapter safety checks; no real MCP transport or worker calls."""
import importlib
import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import urllib.error

import pytest
from offline_support import adapter_context
from fork_microscope.workflow_cli import Client, bounded_json

@pytest.fixture
def adapter(tmp_path, monkeypatch):
    with adapter_context():
        w=importlib.import_module('tools.workflow')
        p={'profile_id':'a'*32,'worker_url':'http://localhost:8767','limits':{'max_seconds':60,'max_samples':100,'max_generated_tokens':2000}}
        for name in ('state_dir','export_dir','evidence_dir'):
            p[name]=tmp_path/name;p[name].mkdir(mode=0o700)
        monkeypatch.setattr(w,'load_profile',lambda:p)
        monkeypatch.setattr(w,'capped',lambda c,p:c)
        yield w,p


def test_bounded_json_rejects_oversize_and_nonobjects():
    stream=io.BytesIO(b'x'*100)
    with pytest.raises(ValueError,match='size'):bounded_json(stream,16)
    assert stream.tell()==17
    for raw in (b'[]',b'null',b'{broken',b'['*2000,b'{"n":NaN}',b'{"n":Infinity}'):
        with pytest.raises(ValueError):bounded_json(io.BytesIO(raw),5000)


def test_client_bounds_and_redacts_errors(monkeypatch):
    import fork_microscope.workflow_cli as cli
    monkeypatch.setattr(cli,'MAX_RESPONSE_BYTES',32)
    class Opener:
        def open(self,*args,**kwargs):return io.BytesIO(b'x'*33)
    monkeypatch.setattr(cli.urllib.request,'build_opener',lambda *a:Opener())
    with pytest.raises(ValueError,match='size'):Client('http://localhost',token='secret').request('workflow')
    class Failure:
        def open(self,*args,**kwargs):
            raise urllib.error.HTTPError('http://localhost',500,'failed',{},io.BytesIO(json.dumps({'error':'secret '+ 'x'*5000}).encode()))
    monkeypatch.setattr(cli.urllib.request,'build_opener',lambda *a:Failure())
    with pytest.raises(ValueError) as exc:Client('http://localhost',token='secret').request('workflow')
    assert 'secret' not in str(exc.value) and len(str(exc.value))<=2000


def test_uncertain_start_preserves_identity_retry_and_config_conflict(adapter,monkeypatch):
    w,p=adapter;c={'limits':p['limits']};requests=[]
    class Worker:
        def request(self,path,payload):
            requests.append(payload)
            if len(requests)==1:raise TimeoutError()
            return {'id':w.digest(payload['request_id'])[:32],'status':'running'}
    monkeypatch.setattr(w,'worker',lambda p:(Worker(),'secret'))
    with pytest.raises(ValueError,match='uncertain start'):w.fork_microscope_start_workflow(c,'stable',True)
    ledger=json.loads((p['state_dir']/'ledger.json').read_text());id=next(iter(ledger['jobs']))
    assert ledger['jobs'][id]['status']=='pending'
    out=w.fork_microscope_start_workflow(c,'stable',True)
    assert out['workflow']['id']==id and requests[0]==requests[1]
    with pytest.raises(ValueError,match='different settings'):w.fork_microscope_start_workflow({'limits':p['limits'],'different':True},'stable',True)
    assert len(requests)==2


@pytest.mark.parametrize('blocked_action',['start','status','export'])
def test_cancel_not_blocked_by_network(adapter,monkeypatch,blocked_action):
    w,p=adapter;c={'limits':p['limits']};key=w.digest({'profile_id':p['profile_id'],'worker_url':p['worker_url'],'request_id':'stable'});id=w.digest(key)[:32]
    if blocked_action!='start':
        with w.ledger(p) as (d,path):
            d['jobs'][id]={'worker_url':p['worker_url'],'limits':p['limits']};w.write_private(path,d)
    entered=threading.Event();release=threading.Event()
    class Worker:
        def request(self,path,payload=None):
            if path=='workflow-cancel':return {'id':id,'status':'cancelled','cancellation_requested':True}
            entered.set();assert release.wait(4)
            return {'id':id,'status':'running'}
        def export(self,path,destination):
            entered.set();assert release.wait(4)
            raise ValueError('synthetic export failure')
    monkeypatch.setattr(w,'worker',lambda p:(Worker(),'secret'))
    actions={'start':lambda:w.fork_microscope_start_workflow(c,'stable',True),'status':lambda:w.operation(id,'status'),'export':lambda:w.fork_microscope_export_workflow(id)}
    with ThreadPoolExecutor(max_workers=2) as pool:
        future=pool.submit(actions[blocked_action])
        assert entered.wait(2)
        cancel=pool.submit(w.fork_microscope_cancel_workflow,id)
        try:assert cancel.result(timeout=1)['workflow']['cancellation_requested']
        finally:release.set()
        if blocked_action=='export':
            with pytest.raises(ValueError,match='synthetic'):future.result(timeout=2)
            assert list(p['export_dir'].iterdir())==[]
        else:future.result(timeout=2)
    saved=json.loads((p['state_dir']/'ledger.json').read_text())['jobs'][id]
    assert saved['status']=='cancelled' and saved['cancellation_requested']


def test_ownership_checked_before_any_worker_access(adapter,monkeypatch):
    w,p=adapter;id='b'*32
    with w.ledger(p) as (d,path):
        d['jobs'][id]={'worker_url':'https://different.example'};w.write_private(path,d)
    monkeypatch.setattr(w,'worker',lambda p:pytest.fail('must reject before worker access'))
    for action in ('status','cancel','resume'):
        with pytest.raises(ValueError,match='not owned'):w.operation(id,action)
    with pytest.raises(ValueError,match='not owned'):w.fork_microscope_export_workflow(id)
    p['profile_id']='c'*32
    with pytest.raises(ValueError,match='does not belong'):w.operation(id,'status')


def test_recursive_output_bounds_and_key_redaction(adapter):
    w,p=adapter
    value={'secret-key':['secret-value'*1000]*1000,'nested':{str(i):['secret']*1000 for i in range(1000)}}
    out=w.redact(value,'secret');encoded=json.dumps(out)
    assert 'secret' not in encoded and len(encoded)<1100000
    deep='secret'
    for _ in range(100):deep=[deep]
    assert 'truncated' in json.dumps(w.redact(deep,'secret'))


def test_redaction_precedes_string_truncation(adapter):
    w,p=adapter;token='TOKEN-THAT-MUST-NOT-LEAK'
    out=w.compact({'message':'x'*1990+token+'tail'},token)
    assert 'TOKEN-' not in out['message']
    assert 'bounded' in out['summary_scope']
