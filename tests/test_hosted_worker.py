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
    def call(self,action,body): return getattr(self.service,action.replace('-','_'))(body)
class Engine:
    starts=0
    def __init__(self,_): pass
    def start(self,jid,c): Engine.starts+=1; return jid
    def status(self,jid): return {'status':'complete','runs':['saved-fixture']}
    def export(self,jid): return json.loads(BUNDLE.read_text())
    def cancel(self,jid): pass

def setup_worker(tmp_path,engine=Engine):
    now=[1000.]
    s=HostedService(MemoryStore(),clock=lambda:now[0],vault=Vault(),catalog=Catalog())
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


def test_failed_job_without_evidence_releases_device_compute(tmp_path):
    class Failed(Engine):
        def status(self,jid): return {'status':'failed'}
    service,worker,job,_=setup_worker(tmp_path,Failed)
    worker.tick()
    session=service.public('alice','session',resource_id=worker.session_id)
    assert session['desired_state']=='terminated'
    assert service.public('alice','artifacts')['artifacts']==[]
