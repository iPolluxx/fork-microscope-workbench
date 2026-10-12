"""No model calls: a saved bundle exercises worker/control/evidence contracts."""
import hashlib
import json
from pathlib import Path
import threading
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import pytest
from fork_microscope.hosted.evidence_delivery import EvidenceDelivery, upload_drive
from fork_microscope.hosted.worker import Worker
from fork_microscope.hosted.service import HostedService
from fork_microscope.hosted.store import MemoryStore

BUNDLE=Path('public/fork-microscope/demo-portfolio.json')

class Catalog:
    def models(self): return []
    def quote(self,b,now): return dict(id='quote',model_id='test',revision='abc',expires_at=now+120,max_duration_seconds=600)
class Vault:
    def put(self,*args): return 'opaque'
class Client:
    def __init__(self,s): self.service=s
    def call(self,action,body): return getattr(self.service,'prepare_artifact' if action=='artifact-prepare' else action.replace('-','_'))(body)
class Engine:
    starts=0
    def __init__(self,_): pass
    def start(self,jid,c): Engine.starts+=1; return jid
    def status(self,jid): return {'status':'complete','runs':['saved-fixture']}
    def export(self,jid): return json.loads(BUNDLE.read_text())
    def cancel(self,jid): pass

def setup_worker(tmp_path,engine=Engine):
    now=[1000.]
    s=HostedService(MemoryStore(),clock=lambda:now[0],vault=Vault(),catalog=Catalog(),credential_validator=lambda uid,key:{'valid':True,'status':'ready'})
    def public(op,b,key): return s.public('alice',op,b,idempotency_key=key)
    public('runpod_put',{'api_key':'test-not-a-real-key'},'key')
    q=public('quote',{},'q')
    session=public('start',{'quote_id':q['id'],'storage_mode':'device','acknowledge_device_loss':True},'s')
    cfg=json.loads(Path('configs/investigation-example.json').read_text())
    cfg['model'].update(model_id='test',revision='abc');cfg['limits']['max_seconds']=100;cfg['lens']=None
    job=public('job',{'session_id':session['id'],'command':{'config':cfg}},'j')
    w=Worker(Client(s),session_id=session['id'],enrollment_token=s.prepare_enrollment(session['id']),root=tmp_path,origin='https://dashboard.example',deadline=1600,clock=lambda:now[0],engine_factory=engine)
    return s,w,job,now

def test_worker_runs_once_and_exports_saved_evidence(tmp_path):
    Engine.starts=0
    s,w,j,now=setup_worker(tmp_path)
    assert w.tick()
    assert Engine.starts==1
    assert s.public('alice','get_job',resource_id=j['id'])['status']=='completed'
    artifact=s.public('alice','artifacts')['artifacts'][0]
    assert 'download_token' not in artifact
    data=(tmp_path/'exports'/f"{j['id']}.json").read_bytes()
    assert hashlib.sha256(data).hexdigest()==artifact['sha256']
    assert json.loads(data)==json.loads(BUNDLE.read_text())
    w.tick(); assert Engine.starts==1

def test_restart_does_not_repeat_generation(tmp_path):
    class Running(Engine):
        def status(self,jid): return {'status':'running'}
    Engine.starts=0
    s,w,j,now=setup_worker(tmp_path,Running)
    w.tick(); assert Engine.starts==1
    reboot=Worker(Client(s),session_id=w.session_id,enrollment_token='',root=tmp_path,origin='https://dashboard.example',deadline=1600,clock=lambda:now[0],engine_factory=Running)
    reboot.tick(); assert Engine.starts==1
    reboot.tick();s.reconcile()
    assert s.public('alice','get_job',resource_id=j['id'])['status']=='interrupted'

def test_file_server_exposes_only_capability_scoped_bundle(tmp_path):
    d=EvidenceDelivery(tmp_path,origin='https://dashboard.example',deadline=time.time()+60)
    record=d.add('a'*32,{'test':'fixture'})
    server=d.server('127.0.0.1',0)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        for path in ['/api/live/status','/bundles/'+'a'*32,'/bundles/../secret']:
            with pytest.raises(HTTPError):urlopen(base+path)
        req=Request(base+'/bundles/'+'a'*32,headers={'Authorization':'Bearer '+record['download_token'],'Origin':d.origin})
        with urlopen(req) as r:
            data=r.read();assert hashlib.sha256(data).hexdigest()==record['sha256']
            assert r.headers['Access-Control-Allow-Origin']==d.origin
        req.add_header('Origin','https://evil.example')
        with pytest.raises(HTTPError):urlopen(req)
    finally:server.shutdown();server.server_close()

def test_delivery_immutability_expiry_and_upload_host(tmp_path):
    now=[100.];d=EvidenceDelivery(tmp_path,origin='https://dashboard.example',deadline=200,clock=lambda:now[0])
    r=d.add('a'*32,{'one':1})
    with pytest.raises(ValueError):d.add('a'*32,{'two':2})
    now[0]=201
    assert d.authorized('a'*32,'Bearer '+r['download_token'],d.origin) is None
    with pytest.raises(ValueError):upload_drive(tmp_path/'a.json','https://evil.example/upload')


def test_download_capability_survives_process_restart(tmp_path):
    first=EvidenceDelivery(tmp_path,origin='https://dashboard.example',deadline=200,clock=lambda:100)
    item=first.add('a'*32,{'saved':True})
    second=EvidenceDelivery(tmp_path,origin=first.origin,deadline=200,clock=lambda:110)
    assert second.authorized('a'*32,'Bearer '+item['download_token'],first.origin).read_bytes()
    assert (tmp_path/'capabilities.json').stat().st_mode & 0o777 == 0o600
    assert (tmp_path/('a'*32+'.json')).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('fail_at', [1, 3, 4])
def test_delivery_persistence_failure_never_shortcuts_retry(tmp_path, monkeypatch, fail_at):
    import os
    delivery=EvidenceDelivery(tmp_path,origin='https://dashboard.example',deadline=200,clock=lambda:100)
    real_sync=os.fsync
    calls=[]
    def interrupted_sync(fd):
        calls.append(fd)
        if len(calls)==fail_at:
            raise OSError('Simulated persistence failure')
        return real_sync(fd)
    monkeypatch.setattr(os,'fsync',interrupted_sync)
    with pytest.raises(OSError,match='persistence failure'):
        delivery.add('a'*32,{'saved':True})
    assert delivery.records=={}
    item=delivery.add('a'*32,{'saved':True})
    restored=EvidenceDelivery(tmp_path,origin=delivery.origin,deadline=200,clock=lambda:110)
    assert restored.records==delivery.records
    raw=restored.authorized('a'*32,'Bearer '+item['download_token'],delivery.origin).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==item['sha256']


def test_failed_job_without_evidence_releases_device_compute(tmp_path):
    class Failed(Engine):
        def status(self,jid): return {'status':'failed'}
    service,worker,job,_=setup_worker(tmp_path,Failed)
    worker.tick()
    session=service.public('alice','session',resource_id=worker.session_id)
    assert session['desired_state']=='terminated'
    assert service.public('alice','artifacts')['artifacts']==[]


@pytest.mark.parametrize('committed', [False,True])
def test_terminal_report_transport_failure_survives_restart_without_replay(tmp_path,committed):
    class Counted(Engine):
        exports=0
        def export(self,jid):
            Counted.exports+=1
            return super().export(jid)
    Engine.starts=0;Counted.exports=0
    s,w,j,_=setup_worker(tmp_path,Counted);call=w.client.call;failed=[False]
    def lost_response(action,body):
        if action=='report' and body['status']=='completed' and not failed[0]:
            failed[0]=True
            if committed: call(action,body)
            raise RuntimeError('simulated terminal report transport failure')
        return call(action,body)
    w.client.call=lost_response
    with pytest.raises(RuntimeError):w.tick()
    assert Engine.starts==Counted.exports==1
    assert w.journal['jobs'][j['id']]['report_pending'] is True
    def no_engine(_): raise AssertionError('Saved terminal reports must not reconstruct or start an engine')
    restarted=Worker(Client(s),session_id=w.session_id,enrollment_token='',root=tmp_path,origin='https://dashboard.example',deadline=1600,clock=w.clock,engine_factory=no_engine)
    restarted.tick()
    assert s.public('alice','get_job',resource_id=j['id'])['status']=='completed'
    assert len(s.public('alice','artifacts')['artifacts'])==1
    assert restarted.journal['jobs'][j['id']]['report_pending'] is False
    assert Engine.starts==Counted.exports==1


def test_export_failure_retries_after_restart_preserving_observed_completion(tmp_path):
    class InterruptedExport(Engine):
        exports=0
        def export(self,jid):
            InterruptedExport.exports+=1
            if InterruptedExport.exports==1: raise RuntimeError('simulated export failure')
            return super().export(jid)
    Engine.starts=0;InterruptedExport.exports=0
    s,w,j,_=setup_worker(tmp_path,InterruptedExport)
    with pytest.raises(RuntimeError):w.tick()
    assert s.public('alice','get_job',resource_id=j['id'])['status']=='saving'
    assert s.public('alice','artifacts')['artifacts']==[]
    assert w.journal['jobs'][j['id']]['terminal_status']=='completed'
    restarted=Worker(Client(s),session_id=w.session_id,enrollment_token='',root=tmp_path,origin='https://dashboard.example',deadline=1600,clock=w.clock,engine_factory=InterruptedExport)
    restarted.tick()
    assert s.public('alice','get_job',resource_id=j['id'])['status']=='completed'
    assert len(s.public('alice','artifacts')['artifacts'])==1
    assert Engine.starts==1 and InterruptedExport.exports==2


def test_retained_interrupted_evidence_is_not_dropped_when_recovery_export_fails(tmp_path):
    class Running(Engine):
        def status(self,jid): return {'status':'running'}
    class Retained(Engine):
        exports=0
        def status(self,jid):return {'status':'interrupted','runs':['saved-fixture']}
        def export(self,jid):
            Retained.exports+=1
            if Retained.exports==1: raise RuntimeError('simulated recovery export failure')
            return super().export(jid)
    Engine.starts=0
    s,w,j,_=setup_worker(tmp_path,Running);w.tick()
    restarted=Worker(Client(s),session_id=w.session_id,enrollment_token='',root=tmp_path,origin='https://dashboard.example',deadline=1600,clock=w.clock,engine_factory=Retained)
    with pytest.raises(RuntimeError):restarted.tick()
    assert s.public('alice','get_job',resource_id=j['id'])['status']=='saving'
    assert s.public('alice','session',resource_id=w.session_id)['desired_state']=='running'
    restarted.tick()
    assert s.public('alice','get_job',resource_id=j['id'])['status']=='interrupted'
    assert len(s.public('alice','artifacts')['artifacts'])==1 and Engine.starts==1


def test_engine_initialization_failure_and_failed_report_are_retryable(tmp_path):
    factories=[]
    def unavailable_engine(_):
        factories.append(True)
        raise RuntimeError('simulated engine initialization failure')
    s,w,j,_=setup_worker(tmp_path,unavailable_engine);call=w.client.call;failed=[False]
    def lost_failure_report(action,body):
        if action=='report' and body['status']=='failed' and not failed[0]:
            failed[0]=True;raise RuntimeError('simulated failure reporting transport error')
        return call(action,body)
    w.client.call=lost_failure_report
    with pytest.raises(RuntimeError):w.tick()
    assert w.journal['jobs'][j['id']]['report_pending'] is True
    w.tick()
    assert factories==[True]
    assert s.public('alice','get_job',resource_id=j['id'])['status']=='failed'
    assert s.public('alice','session',resource_id=w.session_id)['desired_state']=='terminated'


def test_local_deadline_stops_before_enrollment_heartbeat_or_dispatch(tmp_path):
    Engine.starts=0
    s,w,j,now=setup_worker(tmp_path)
    calls=[];call=w.client.call
    def recorded(action,body):calls.append(action);return call(action,body)
    w.client.call=recorded;now[0]=w.deadline
    assert w.tick() is False
    w.heartbeat()
    assert calls==[] and Engine.starts==0
    assert s.public('alice','get_job',resource_id=j['id'])['status']=='queued'


def test_local_deadline_cancels_running_engine_with_control_plane_unavailable(tmp_path):
    cancelled=[]
    class Running(Engine):
        def status(self,jid):return {'status':'running'}
        def cancel(self,jid):cancelled.append(jid)
    s,w,j,now=setup_worker(tmp_path,Running);w.tick()
    w.client.call=lambda *args:(_ for _ in ()).throw(AssertionError('Expired worker must not contact the control plane'))
    now[0]=w.deadline
    assert w.tick() is False and cancelled==[j['id']]


def test_expired_poll_response_never_starts_generation(tmp_path):
    Engine.starts=0;s,w,j,now=setup_worker(tmp_path);call=w.client.call
    def delayed_poll(action,body):
        result=call(action,body)
        if action=='poll':now[0]=w.deadline
        return result
    w.client.call=delayed_poll
    assert w.tick() is False and Engine.starts==0 and w.journal['jobs']=={}


def test_crash_after_engine_launch_before_journal_save_recovers_evidence_without_start(tmp_path):
    class CrashAfterLaunch(Engine):
        def __init__(self,root):self.root=Path(root)
        def start(self,jid,config):
            Engine.starts+=1
            (self.root/'durable-workflow.json').write_text(json.dumps(dict(job=jid,workflow='saved-workflow')))
            raise SystemExit('simulated process crash after workflow was saved')
        def recover(self,jid):
            value=json.loads((self.root/'durable-workflow.json').read_text())
            assert value['job']==jid
            return value['workflow']
        def status(self,jid):
            assert jid=='saved-workflow'
            return {'status':'interrupted','runs':['saved-fixture']}
    Engine.starts=0
    s,w,j,_=setup_worker(tmp_path,CrashAfterLaunch)
    with pytest.raises(SystemExit):w.tick()
    assert w.journal['jobs'][j['id']]['state']=='starting' and Engine.starts==1
    restarted=Worker(Client(s),session_id=w.session_id,enrollment_token='',root=tmp_path,origin='https://dashboard.example',deadline=1600,clock=w.clock,engine_factory=CrashAfterLaunch)
    restarted.tick()
    assert Engine.starts==1 and len(s.public('alice','artifacts')['artifacts'])==1
    assert s.public('alice','get_job',resource_id=j['id'])['status']=='interrupted'


def test_engine_success_without_evidence_fails_and_releases_compute(tmp_path):
    class Empty(Engine):
        def status(self,jid):return {'status':'complete'}
    s,w,j,_=setup_worker(tmp_path,Empty)
    w.tick()
    assert s.public('alice','get_job',resource_id=j['id'])['status']=='failed'
    assert s.public('alice','session',resource_id=w.session_id)['desired_state']=='terminated'
    assert s.public('alice','artifacts')['artifacts']==[]


def test_real_engine_recovery_reads_existing_workflow_catalog_without_launch(tmp_path):
    from types import SimpleNamespace
    from fork_microscope.hosted.worker import Engine as HostedEngine
    from fork_microscope.investigation_bundle import digest
    from fork_microscope.investigation_workflow import WorkflowManager
    job_id='a'*32;workflow_id=digest(job_id)[:32]
    root=tmp_path/'workflow-jobs';root.mkdir()
    (root/(workflow_id+'.json')).write_text(json.dumps(dict(id=workflow_id,status='interrupted')))
    engine=HostedEngine.__new__(HostedEngine)
    engine.manager=WorkflowManager(SimpleNamespace(),root)
    assert engine.recover(job_id)==workflow_id
    assert engine.manager.active is None


def test_cooperative_cancel_exports_partial_evidence_and_keeps_device_receipt_open(tmp_path):
    class Cancellable(Engine):
        def __init__(self,_):self.cancelled=False
        def status(self,jid):
            return {'status':'cancelled','runs':['saved-fixture']} if self.cancelled else {'status':'running'}
        def cancel(self,jid):self.cancelled=True
    Engine.starts=0
    s,w,j,_=setup_worker(tmp_path,Cancellable);w.tick()
    s.public('alice','cancel',resource_id=j['id'],idempotency_key='cancel')
    w.tick()
    assert Engine.starts==1 and s.public('alice','get_job',resource_id=j['id'])['status']=='cancelled'
    artifact=s.public('alice','artifacts')['artifacts'][0]
    data=(tmp_path/'exports'/(j['id']+'.json')).read_bytes()
    assert hashlib.sha256(data).hexdigest()==artifact['sha256']
    assert s.public('alice','session',resource_id=w.session_id)['desired_state']=='running'
    s.public('alice','received',resource_id=artifact['id'],idempotency_key='receipt')
    assert s.public('alice','session',resource_id=w.session_id)['desired_state']=='terminated'


class UploadResponse:
    def __init__(self,status,headers=None,value=None):self.status=status;self.headers=headers or {};self.value=value or {}
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,limit):return json.dumps(self.value).encode()[:limit]


def test_resumed_drive_upload_queries_progress_before_sending_remaining_bytes(tmp_path):
    data=b'a'*7+b'b'*9;path=tmp_path/'bundle.json';path.write_bytes(data)
    requests=[]
    def opener(request,timeout):
        requests.append(request)
        if len(requests)==1:
            assert request.data==b'' and request.get_header('Content-range')=='bytes */16'
            return UploadResponse(308,{'Range':'bytes=0-6'})
        assert request.data==b'b'*9 and request.get_header('Content-range')=='bytes 7-15/16'
        return UploadResponse(200,value={'id':'fake-drive-file'})
    result=upload_drive(path,'https://www.googleapis.com/upload/drive/v3/files?upload_id=FAKE',opener=opener,clock=lambda:100,deadline=200,resume=True)
    assert result['id']=='fake-drive-file' and len(requests)==2


def test_resumed_drive_upload_recognizes_previously_completed_file_without_resending(tmp_path):
    path=tmp_path/'bundle.json';path.write_bytes(b'saved-fixture')
    requests=[]
    def opener(request,timeout):
        requests.append(request)
        assert request.data==b'' and request.get_header('Content-range')=='bytes */13'
        return UploadResponse(200,value={'id':'fake-drive-file'})
    assert upload_drive(path,'https://www.googleapis.com/upload/drive/v3/files?upload_id=FAKE',opener=opener,clock=lambda:100,deadline=200,resume=True)['id']=='fake-drive-file'
    assert len(requests)==1


def test_upload_lost_completion_response_queries_instead_of_resending_committed_bytes(tmp_path):
    from urllib.error import URLError
    path=tmp_path/'bundle.json';path.write_bytes(b'saved-fixture')
    requests=[]
    def opener(request,timeout):
        requests.append(request)
        if len(requests)==1:return UploadResponse(308)
        if len(requests)==2:
            assert request.data==b'saved-fixture'
            raise URLError('simulated loss after final bytes committed')
        assert request.data==b'' and request.get_header('Content-range')=='bytes */13'
        return UploadResponse(200,value={'id':'fake-drive-file'})
    assert upload_drive(path,'https://www.googleapis.com/upload/drive/v3/files?upload_id=FAKE',opener=opener,clock=lambda:100,deadline=200,resume=True)['id']=='fake-drive-file'
    assert len(requests)==3 and sum(bool(r.data) for r in requests)==1


def test_resumed_drive_upload_rejects_impossible_progress_without_data_send(tmp_path):
    path=tmp_path/'bundle.json';path.write_bytes(b'saved-fixture')
    requests=[]
    def opener(request,timeout):requests.append(request);return UploadResponse(308,{'Range':'bytes=0-999'})
    with pytest.raises(RuntimeError,match='Invalid Drive upload progress'):
        upload_drive(path,'https://www.googleapis.com/upload/drive/v3/files?upload_id=FAKE',opener=opener,clock=lambda:100,deadline=200,resume=True)
    assert len(requests)==1 and requests[0].data==b''


def test_worker_reuses_saved_drive_grant_and_artifact_after_upload_restart(tmp_path,monkeypatch):
    class Counted(Engine):
        exports=0
        def export(self,jid):Counted.exports+=1;return super().export(jid)
    Engine.starts=0;Counted.exports=0
    s,w,j,_=setup_worker(tmp_path,Counted)
    s.store.transaction(lambda tx:tx.set('sessions',w.session_id,dict(tx.get('sessions',w.session_id),storage_mode='drive')))
    prepares=[];uploads=[]
    def prepare(session,job,body):
        prepares.append(body)
        return dict(file_ref='fake-opaque-ref',upload_url='https://www.googleapis.com/upload/drive/v3/files?upload_id=FAKE')
    s.artifact_prepare=prepare;s.artifact_verify=lambda *args:None
    def upload(path,url,**kwargs):
        uploads.append((path.read_bytes(),url,kwargs['resume']))
        if len(uploads)==1:raise RuntimeError('simulated interruption during upload')
        return {'id':'fake-drive-file'}
    monkeypatch.setattr('fork_microscope.hosted.worker.upload_drive',upload)
    with pytest.raises(RuntimeError):w.tick()
    assert Engine.starts==Counted.exports==1 and len(prepares)==1
    def no_engine(_):raise AssertionError('Retained upload must not reconstruct or start an engine')
    restarted=Worker(Client(s),session_id=w.session_id,enrollment_token='',root=tmp_path,origin='https://dashboard.example',deadline=1600,clock=w.clock,engine_factory=no_engine)
    restarted.tick()
    assert Engine.starts==Counted.exports==1 and len(prepares)==1 and len(uploads)==2
    assert uploads[0]==uploads[1] and uploads[1][2] is True
    assert s.public('alice','get_job',resource_id=j['id'])['status']=='completed'
    assert s.public('alice','session',resource_id=w.session_id)['desired_state']=='terminated'
    assert s.public('alice','artifacts')['artifacts'][0]['storage_mode']=='drive'
