"""Saved-fixture integration, real HTTP routing; no network/model/GPU execution."""
import hashlib
import json
from pathlib import Path
import threading
from urllib.request import Request, urlopen
import pytest
from fastapi.testclient import TestClient
from fork_microscope.hosted.bootstrap import create_test_apps
from fork_microscope.hosted.provider import FakeRunPod
from fork_microscope.hosted.worker import Worker

BASE='/api/hosted/v1/'
class NoGoogle:
    def request(self,*a,**k): raise AssertionError('Device fixture must never call Google')
class SavedEngine:
    def __init__(self,root): self.starts=0
    def start(self,job_id,config): self.starts+=1;return job_id
    def status(self,job_id): return {'status':'complete','runs':['saved-fixture']}
    def export(self,job_id): return json.loads(Path('public/fork-microscope/demo-portfolio.json').read_text())
    def cancel(self,job_id): pass
class HttpWorker:
    def __init__(self,client):self.client=client
    def call(self,action,body):
        response=self.client.post(BASE+'worker/'+action,json=body)
        assert response.status_code==200,response.text
        return response.json()

@pytest.fixture
def system():
    now=[1000.]
    provider=FakeRunPod([{'id':'gpu','manufacturer':'NVIDIA','secure':True,'memory':24,'price':{'secure':.5},'availability':'HIGH'}])
    apps=create_test_apps(users={u:{'uid':u,'email':u+'@example.test'} for u in ('alice','bob')},runpod=provider,google_transport=NoGoogle(),clock=lambda:now[0])
    return apps,TestClient(apps.public,base_url=apps.public_config.public_url),TestClient(apps.controller),provider,now

def public(client,uid,path,body=None,key=None,method='POST'):
    headers={'Authorization':'Bearer '+uid}
    if key:headers['Idempotency-Key']=key
    return client.request(method,BASE+path,json=body,headers=headers)
def start(client,uid,prefix=""):
    connected=public(client,uid,'connections/runpod',{'api_key':uid+'-test-private-key'},prefix+'credential','PUT')
    assert connected.status_code==200,connected.text
    models=public(client,uid,'models',method='GET').json()['models']
    quote=public(client,uid,'quotes',{'model_id':models[0]['id'],'max_duration_seconds':1800},prefix+'quote')
    assert quote.status_code==200,quote.text
    session=public(client,uid,'sessions',{'quote_id':quote.json()['id'],'storage_mode':'device','acknowledge_device_loss':True},prefix+'session')
    assert session.status_code==200,session.text
    return session.json(),quote.json()
def reconcile(apps,controller):
    response=controller.post('/internal/reconcile',json={},headers=apps.internal('scheduler'))
    assert response.status_code==200,response.text

def test_split_apps_two_owners_fixture_export_receipt_and_verified_cleanup(system,tmp_path):
    apps,client,controller,provider,now=system
    session,quote=start(client,'alice');other,_=start(client,'bob')
    reconcile(apps,controller)
    assert provider.creates==2
    assert public(client,'bob','sessions/'+session['id'],method='GET').status_code==404
    pod=provider.pods[public(client,'alice','sessions/'+session['id'],method='GET').json()['provider_ref']]
    assert not any('RUNPOD' in key or 'DRIVE' in key for key in pod['env'])
    cfg=json.loads(Path('configs/investigation-example.json').read_text())
    cfg['model'].update(model_id=quote['model_id'],revision=quote['revision']);cfg['lens']=None;cfg['limits']['max_seconds']=100
    job=public(client,'alice','jobs',{'session_id':session['id'],'command':{'config':cfg}},'job')
    assert job.status_code==200,job.text
    worker=Worker(HttpWorker(client),session_id=session['id'],enrollment_token=pod['env']['FM_ENROLLMENT_TOKEN'],root=tmp_path,origin=apps.public_config.dashboard_origin,deadline=session['expires_at'],clock=lambda:now[0],engine_factory=SavedEngine)
    assert worker.tick();assert worker.engine.starts==1
    saved=public(client,'alice','artifacts',method='GET').json()['artifacts'][0]
    assert 'download_token' not in saved
    assert public(client,'bob','artifacts/'+saved['id']+'/download',method='GET').status_code==404
    # This URL is derived from provider identity; actual evidence bytes use localhost only.
    download=public(client,'alice','artifacts/'+saved['id']+'/download',method='GET')
    assert download.status_code==200,download.text
    assert download.json()['url'].endswith('/bundles/'+saved['id'])
    server=worker.delivery.server('127.0.0.1',0)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        request=Request('http://127.0.0.1:%s/bundles/%s'%(server.server_port,saved['id']),headers={'Authorization':'Bearer '+download.json()['download_token'],'Origin':apps.public_config.dashboard_origin})
        with urlopen(request) as response:data=response.read()
        assert hashlib.sha256(data).hexdigest()==saved['sha256']
        assert json.loads(data)==json.loads(Path('public/fork-microscope/demo-portfolio.json').read_text())
        (tmp_path/'received.json').write_bytes(data)
        ack=public(client,'alice','artifacts/'+saved['id']+'/received',{},'receipt')
        assert ack.status_code==200,ack.text
        assert ack.json()['desired_state']=='terminated'
    finally:server.shutdown();server.server_close()
    reconcile(apps,controller)
    state=public(client,'alice','sessions/'+session['id'],method='GET').json()
    assert state['observed_state']=='terminated' and state['cleanup_verified']
    assert len(provider.pods)==1
    # Fresh credentials and a second session work after confirmed provider cleanup.
    newer,_=start(client,'alice','second-');assert newer['id']!=session['id']

def test_uncertain_create_and_duplicate_resource_cleanup_no_redelivery(system):
    apps,client,controller,provider,now=system
    session,_=start(client,'alice');provider.uncertain_create=True
    reconcile(apps,controller);reconcile(apps,controller)
    assert provider.creates==1
    ref=public(client,'alice','sessions/'+session['id'],method='GET').json()['provider_ref']
    assert ref in provider.pods
    provider.pods['duplicate']=dict(provider.pods[ref],id='duplicate')
    provider.pods['unrelated']={'id':'unrelated','name':'personal-pod'}
    reconcile(apps,controller)
    assert public(client,'alice','sessions/'+session['id'],method='GET').json()['desired_state']=='terminated'
    reconcile(apps,controller)
    assert set(provider.pods)=={'unrelated'}
    assert provider.creates==1

def test_no_gpu_quote_fails_before_any_launch_and_internal_identity_separation(system):
    apps,client,controller,provider,_=system
    public(client,'alice','connections/runpod',{'api_key':'test-private-key'},'credential','PUT')
    provider.catalog[0]['availability']='NONE'
    response=public(client,'alice','quotes',{'model_id':'Qwen/Qwen2.5-1.5B-Instruct'},'quote')
    assert response.status_code==422,response.text
    assert provider.creates==0
    assert controller.post('/internal/reconcile',json={},headers=apps.internal('public')).status_code==403
    assert controller.post('/internal/catalog/quote',json={'owner_uid':'alice'},headers=apps.internal('scheduler')).status_code==403

def test_user_quote_allowance_is_preserved_across_public_controller_boundary(system):
    apps,client,controller,provider,_=system
    public(client,'alice','connections/runpod',{'api_key':'test-private-key'},'credential','PUT')
    response=public(client,'alice','quotes',{'model_id':'Qwen/Qwen2.5-1.5B-Instruct','max_duration_seconds':1800,'max_usd':0.00001},'too-small')
    assert response.status_code==422,response.text
    assert provider.creates==0

def test_termination_before_dispatch_clears_queued_private_command(system):
    apps,client,controller,provider,_=system
    session,quote=start(client,'alice')
    cfg=json.loads(Path('configs/investigation-example.json').read_text())
    cfg['model'].update(model_id=quote['model_id'],revision=quote['revision']);cfg['lens']=None;cfg['limits']['max_seconds']=100
    job=public(client,'alice','jobs',{'session_id':session['id'],'command':{'config':cfg}},'job').json()
    assert public(client,'alice','sessions/'+session['id']+'/terminate',{},'terminate').status_code==200
    reconcile(apps,controller)
    stored=apps.store.transaction(lambda tx:tx.get('jobs',job['id']))
    assert stored['status']=='cancelled'
    assert 'command' not in stored
    assert provider.creates==0
