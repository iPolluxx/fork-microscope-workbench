import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from fork_microscope.hosted import HostedService, MemoryStore, HostedError
from fork_microscope.hosted.api import create_app

class Auth:
    def verify(self, token):
        if token not in {'alice','bob'}: raise ValueError()
        return {'uid':token}
class Vault:
    def put(self,*args): return 'opaque-secret'
    def delete(self,*args): pass
class Catalog:
    def models(self): return [{'model_id':'test','revision':'abc'}]
    def quote(self,b,now): return {'id':'quote-'+b['owner_uid'],'model_id':'test','revision':'abc','expires_at':now+300,'max_duration_seconds':600}
@pytest.fixture
def setup():
    now=[1000.]
    service=HostedService(MemoryStore(),clock=lambda:now[0],vault=Vault(),catalog=Catalog())
    client=TestClient(create_app(service,Auth(),worker_enabled=True))
    return service,client,now

def request(c,path,body,uid='alice',key='k',method='POST'):
    return c.request(method,'/api/hosted/v1/'+path,json=body,headers={'Authorization':'Bearer '+uid,'Idempotency-Key':key})
def session(c):
    assert request(c,'connections/runpod',{'api_key':'123456789012'},key='conn',method='PUT').status_code==200
    assert request(c,'quotes',{},key='quote').status_code==200
    result=request(c,'sessions',{'quote_id':'quote-alice','storage_mode':'device','acknowledge_device_loss':True},key='start')
    assert result.status_code==200,result.text
    return result.json()['id']
def config():
    c=json.loads(Path('configs/investigation-example.json').read_text());c['model'].update(model_id='test',revision='abc');c['limits']['max_seconds']=100;c['lens']=None;return c

def test_owner_idempotency_and_http_auth(setup):
    s,c,_=setup; sid=session(c)
    assert c.get('/api/hosted/v1/me').status_code==401
    assert request(c,'sessions/'+sid,{},uid='bob',method='GET').status_code==404
    response=request(c,'sessions',{'quote_id':'quote-alice','storage_mode':'device','acknowledge_device_loss':True},key='start')
    assert response.json()['id']==sid
    assert request(c,'sessions',{},key='start').status_code==409
    assert request(c,'jobs',{'session_id':sid,'command':{'config':config()}},key='job').status_code==200
    assert request(c,'jobs',{'session_id':sid,'command':{'config':config()}},key='job2').status_code==409

def test_atomic_active_session_race(setup):
    s,c,_=setup;session(c)
    def attempt(n): return request(c,'sessions',{'quote_id':'quote-alice','storage_mode':'device','acknowledge_device_loss':True},key=str(n)).status_code
    with ThreadPoolExecutor(max_workers=8) as pool: assert set(pool.map(attempt,range(20)))=={409}

def test_worker_no_redelivery_fencing_cancel_and_expiry(setup):
    s,c,now=setup;sid=session(c)
    jid=request(c,'jobs',{'session_id':sid,'command':{'config':config()}},key='job').json()['id']
    token=s.prepare_enrollment(sid)
    enrollment=c.post('/api/hosted/v1/worker/enroll',json={'session_id':sid,'token':token}).json()
    assert c.post('/api/hosted/v1/worker/enroll',json={'session_id':sid,'token':token}).status_code==401
    b={'session_id':sid,'worker_token':enrollment['worker_token'],'epoch':enrollment['epoch']}
    assert c.post('/api/hosted/v1/worker/poll',json=b).json()['action']=='run'
    assert c.post('/api/hosted/v1/worker/poll',json=b).json()['action']=='wait'
    assert c.post('/api/hosted/v1/worker/heartbeat',json=dict(b,seq=1)).status_code==200
    assert c.post('/api/hosted/v1/worker/heartbeat',json=dict(b,seq=1)).status_code==409
    request(c,'jobs/'+jid+'/cancel',{},key='cancel')
    assert c.post('/api/hosted/v1/worker/poll',json=b).json()['action']=='cancel'
    now[0]+=100;s.reconcile()
    assert c.post('/api/hosted/v1/worker/poll',json=b).status_code==401
    assert request(c,'jobs/'+jid,{},method='GET').json()['status']=='interrupted'

def test_artifact_owner_download_and_redaction(setup):
    s,c,_=setup;sid=session(c)
    s.store.transaction(lambda tx: tx.set('sessions',sid,dict(tx.get('sessions',sid),provider_ref='pod123')))
    jid=request(c,'jobs',{'session_id':sid,'command':{'config':config()}},key='job').json()['id']
    e=s.enroll({'session_id':sid,'token':s.prepare_enrollment(sid)})
    b={'session_id':sid,'worker_token':e['worker_token'],'epoch':e['epoch']};s.poll(b)
    response=c.post('/api/hosted/v1/worker/report',json=dict(b,job_id=jid,status='completed',artifacts=[{'id':'artifact1','sha256':'a'*64,'file_ref':'artifact1','size_bytes':100,'download_token':'x'*32}]))
    assert response.status_code==200,response.text
    listing=request(c,'artifacts',{},method='GET').json()
    assert 'download_token' not in listing['artifacts'][0]
    assert request(c,'artifacts/artifact1/download',{},uid='bob',method='GET').status_code==404
    assert request(c,'artifacts/artifact1/download',{},method='GET').json()['download_token']=='x'*32

def test_transaction_rollback(setup):
    s,c,_=setup
    def bad(tx): tx.set('x','id',{'a':1});raise ValueError()
    with pytest.raises(ValueError): s.store.transaction(bad)
    assert s.store.transaction(lambda tx: tx.get('x','id')) is None

def test_credentials_retain_cleanup_key_and_failed_write_releases_claim(setup):
    s,c,_=setup;sid=session(c)
    assert request(c,'connections/runpod',{},key='delete',method='DELETE').status_code==409
    assert request(c,'connections/runpod',{'api_key':'replacement123'},key='replace',method='PUT').status_code==409
    s.store.transaction(lambda tx:tx.set('sessions',sid,dict(tx.get('sessions',sid),observed_state='terminated',cleanup_verified=True)))
    class FailingVault:
        def put(self,*a): raise RuntimeError('secret failure')
        def delete(self,*a): raise AssertionError('Old credential must survive failed put')
    s.vault=FailingVault()
    with pytest.raises(RuntimeError): s.public('alice','runpod_put',{'api_key':'replacement123'},idempotency_key='fails')
    conn=s.store.transaction(lambda tx:tx.get('connections','alice_runpod'))
    assert conn['credential_ref']=='opaque-secret'
    assert 'mutation_claim' not in conn

def test_drive_status_and_admission_use_security_connection_schema(setup):
    s,c,_=setup
    request(c,'connections/runpod',{'api_key':'123456789012'},key='conn',method='PUT')
    request(c,'quotes',{},key='quote')
    assert request(c,'sessions',{'quote_id':'quote-alice','storage_mode':'drive'},key='start').status_code==409
    s.store.transaction(lambda tx:tx.set('drive_connections','alice',{'owner':'alice','status':'connected'}))
    assert request(c,'connections/drive',{},method='GET').json()['connected']
    assert request(c,'sessions',{'quote_id':'quote-alice','storage_mode':'drive'},key='start').status_code==200

def test_partial_artifacts_and_size_bounds(setup):
    s,c,_=setup;sid=session(c)
    jid=request(c,'jobs',{'session_id':sid,'command':{'config':config()}},key='job').json()['id']
    e=s.enroll({'session_id':sid,'token':s.prepare_enrollment(sid)})
    b={'session_id':sid,'worker_token':e['worker_token'],'epoch':e['epoch']};s.poll(b)
    artifact={'id':'partial1','sha256':'a'*64,'size_bytes':67108865,'download_token':'x'*32}
    assert c.post('/api/hosted/v1/worker/report',json=dict(b,job_id=jid,status='cancelled',artifacts=[artifact])).status_code==422
    artifact['size_bytes']=100
    assert c.post('/api/hosted/v1/worker/report',json=dict(b,job_id=jid,status='cancelled',artifacts=[artifact])).status_code==200
    assert len(request(c,'artifacts',{},method='GET').json()['artifacts'])==1

def test_invalid_config_and_quote_expiry(setup):
    s,c,now=setup;sid=session(c)
    bad=config();bad['scan']['temperature']=None
    assert request(c,'jobs',{'session_id':sid,'command':{'config':bad}},key='bad').status_code==422
    wrong=config();wrong['model']['revision']='another-pin'
    assert request(c,'jobs',{'session_id':sid,'command':{'config':wrong}},key='wrong').status_code==422
    now[0]+=301
    assert request(c,'sessions',{'quote_id':'quote-alice','storage_mode':'device','acknowledge_device_loss':True},key='expired').status_code==409


def test_hosted_lens_requires_explicit_catalog_allowlist(setup):
    s,c,_=setup;sid=session(c)
    bad=config();bad['lens']={'profile':'../../private','layers':[1],'before':1,'after':1,'top_k':2}
    assert request(c,'jobs',{'session_id':sid,'command':{'config':bad}},key='lens').status_code==422

def test_device_receipt_owner_and_idempotency_terminate_only_after_all_artifacts(setup):
    s,c,_=setup;sid=session(c)
    jid=request(c,'jobs',{'session_id':sid,'command':{'config':config()}},key='job').json()['id']
    e=s.enroll({'session_id':sid,'token':s.prepare_enrollment(sid)})
    b={'session_id':sid,'worker_token':e['worker_token'],'epoch':e['epoch']};s.poll(b)
    artifacts=[{'id':aid,'sha256':'a'*64,'size_bytes':100,'download_token':'x'*32} for aid in ('artifact1','artifact2')]
    s.report(dict(b,job_id=jid,status='completed',artifacts=artifacts))
    assert request(c,'artifacts/artifact1/received',{},uid='bob',key='receipt').status_code==404
    assert request(c,'artifacts/artifact1/received',{},key='receipt1').json()['desired_state']=='running'
    assert request(c,'artifacts/artifact2/received',{},key='receipt2').json()['desired_state']=='terminated'
    assert request(c,'artifacts/artifact2/received',{},key='receipt2').json()['received']

def test_drive_automatic_termination_after_verified_export(setup):
    s,c,_=setup
    request(c,'connections/runpod',{'api_key':'123456789012'},key='conn',method='PUT');request(c,'quotes',{},key='quote')
    s.store.transaction(lambda tx:tx.set('drive_connections','alice',{'owner':'alice','status':'connected'}))
    sid=request(c,'sessions',{'quote_id':'quote-alice','storage_mode':'drive'},key='start').json()['id']
    jid=request(c,'jobs',{'session_id':sid,'command':{'config':config()}},key='job').json()['id']
    e=s.enroll({'session_id':sid,'token':s.prepare_enrollment(sid)})
    b={'session_id':sid,'worker_token':e['worker_token'],'epoch':e['epoch']};s.poll(b)
    report=dict(b,job_id=jid,status='completed',artifacts=[{'id':'drive1','sha256':'a'*64,'size_bytes':100,'file_ref':'df_file'}])
    with pytest.raises(HostedError): s.report(report)
    assert request(c,'sessions/'+sid,{},method='GET').json()['desired_state']=='running'
    verified=[];s.artifact_verify=lambda *a:verified.append(a)
    s.report(report)
    assert verified and request(c,'sessions/'+sid,{},method='GET').json()['desired_state']=='terminated'

def test_failed_session_blocks_replacement_until_provider_cleanup_verified(setup):
    s,c,_=setup;sid=session(c)
    s.store.transaction(lambda tx:tx.set('sessions',sid,dict(tx.get('sessions',sid),observed_state='failed')))
    body={'quote_id':'quote-alice','storage_mode':'device','acknowledge_device_loss':True}
    assert request(c,'sessions',body,key='replacement').status_code==409
    s.store.transaction(lambda tx:tx.set('sessions',sid,dict(tx.get('sessions',sid),observed_state='terminated',cleanup_verified=True)))
    assert request(c,'sessions',body,key='replacement').status_code==200
