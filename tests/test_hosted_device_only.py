"""Device-only production assembly and routes; all lifecycle execution uses explicit fakes."""
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fork_microscope.hosted import bootstrap as bs
from fork_microscope.hosted.credentials import GoogleSecretVault, VaultError
from fork_microscope.hosted.provider import FakeRunPod
from fork_microscope.hosted.service import HostedError
from fork_microscope.hosted.store import MemoryStore

ROOT=Path(__file__).resolve().parents[1]
GPU={'id':'fake-gpu','manufacturer':'NVIDIA','secure':True,'memory':24,'availability':'HIGH','price':{'secure':0.5}}
FAKE_KEY='FAKE-ONLY-device-test-key'


def device_environment(**overrides):
    env={k:v for k,v in bs.sample_environment().items() if not k.startswith('FM_DRIVE_')}
    env.update(FM_DRIVE_ENABLED='false')
    env.update(overrides)
    return env


def config():
    value=json.loads((ROOT/'configs/investigation-example.json').read_text())
    value['model'].update(model_id=bs.MODELS[0]['id'],revision=bs.MODELS[0]['revision'])
    value['limits']['max_seconds']=100
    value['lens']=None
    return value


def forbidden(*args,**kwargs):
    raise AssertionError('Disabled Drive must not be accessed')


class NoDriveTransport:
    request=staticmethod(forbidden)
    stream=staticmethod(forbidden)


@pytest.fixture
def apps():
    now=[1000.]
    provider=FakeRunPod([GPU])
    apps=bs.create_test_apps(users={'alice-token':{'uid':'alice','email':'alice@example.test'},'bob-token':{'uid':'bob','email':'bob@example.test'}},
        runpod=provider,google_transport=NoDriveTransport(),clock=lambda:now[0],env=device_environment())
    return apps,provider,now


def test_config_skips_every_drive_setting_and_secret_file(monkeypatch):
    class GuardedEnvironment(dict):
        def get(self,name,*args):
            if name.startswith('FM_DRIVE_') and name!='FM_DRIVE_ENABLED': forbidden()
            return super().get(name,*args)
    monkeypatch.setattr(bs,'_drive_secret',forbidden)
    env=GuardedEnvironment(device_environment(FM_DRIVE_CLIENT_SECRET_FILE='/must/not/read',FM_DRIVE_CLIENT_ID='invalid',FM_DRIVE_REDIRECT_URI='invalid',FM_DRIVE_POST_AUTH_URL='invalid'))
    for cls in (bs.PublicConfig,bs.ControllerConfig):
        result=cls.from_env(env)
        assert result.drive_enabled is False and result.drive_client_secret is None
        assert result.drive_client_id==result.drive_redirect_uri==''
        with pytest.raises(bs.ConfigError): result.drive_config()
    assert bs.PublicConfig.from_env(env).post_auth_url==''


def test_production_factories_construct_without_any_drive_components(monkeypatch):
    # Persistence is the only factory dependency replaced here. Real Firebase,
    # OIDC, Secret Manager, and RunPod classes construct lazily without network.
    monkeypatch.setattr(bs,'_firestore',lambda cfg:MemoryStore())
    for name in ('DriveAuthorization','DriveController','DriveConfig','DriveArtifacts','_drive_secret'):
        monkeypatch.setattr(bs,name,forbidden)
    env=device_environment(FM_HOSTED_ENV='production')
    public=bs.create_public_app(env)
    controller=bs.create_controller_app(env)
    assert public.state.service.drive_enabled is controller.state.service.drive_enabled is False
    assert isinstance(public.state.service.vault,bs.WriteOnlyVault)
    assert isinstance(public.state.service.vault._inner,GoogleSecretVault)
    assert isinstance(controller.state.service.vault,GoogleSecretVault)
    with pytest.raises(VaultError): public.state.service.vault.get('opaque')
    c=TestClient(public)
    assert c.get(bs.BASE+'/me').status_code==401
    assert c.post(bs.BASE+'/worker/enroll',json={'session_id':'unknown','token':'invalid'}).status_code==401
    assert TestClient(controller).post('/internal/reconcile',json={}).status_code==401


def test_disabled_drive_routes_are_explicit_and_keep_authentication(apps):
    apps,_,_=apps
    public=TestClient(apps.public)
    controller=TestClient(apps.controller)
    user={'Authorization':'Bearer alice-token'}
    status=public.get(bs.BASE+'/connections/drive',headers=user)
    assert status.json()=={'enabled':False,'connected':False}
    assert status.headers['cache-control']=='no-store'
    assert apps.public.state.service.public('alice','drive')==status.json()
    for suffix in ('begin','disconnect'):
        path=bs.BASE+'/connections/drive/'+suffix
        assert public.post(path).status_code==401
        assert public.post(path,headers=user,json={}).status_code==503
    assert public.get(bs.CALLBACK_PATH,follow_redirects=False).status_code==503
    for op in ('status','disconnect','artifact-prepare','artifact-verify','download'):
        path='/internal/drive/'+op
        assert controller.post(path,json={'owner_uid':'alice'}).status_code==401
        assert controller.post(path,json={'owner_uid':'alice'},headers=apps.internal('scheduler')).status_code==403
        response=controller.post(path,json={'owner_uid':'alice'},headers=apps.internal('public'))
        assert response.status_code==(200 if op=='status' else 503)
        if op=='status': assert response.json()=={'enabled':False,'connected':False}
    service=apps.public.state.service
    for operation,args in ((service.artifact_prepare,({'storage_mode':'drive'},{},{})),(service.artifact_verify,({}, {}, []))):
        with pytest.raises(HostedError) as error: operation(*args)
        assert error.value.status==503


def connect(service):
    return service.public('alice','runpod_put',{'api_key':FAKE_KEY},idempotency_key='connect')


def plan_body(mode='device'):
    return dict(config=config(),usd_allowance=1,max_duration_seconds=600,storage_mode=mode,acknowledge_device_loss=True)


def test_disabled_drive_rejects_stale_connections_plans_jobs_and_downloads(apps):
    apps,provider,_=apps
    s=apps.public.state.service
    connect(s)
    s.store.transaction(lambda tx:tx.set('drive_connections','alice',{'owner':'alice','status':'connected'}))
    # These records represent a deployment that had Drive enabled earlier.
    s.drive_enabled=True
    plan=s.public('alice','prepare_plan',plan_body('drive'),idempotency_key='old-plan')
    quote=s.public('alice','quote',{'model_id':bs.MODELS[0]['id'],'max_duration_seconds':600},idempotency_key='old-quote')
    session=s.public('alice','start',{'quote_id':quote['id'],'storage_mode':'drive'},idempotency_key='old-session')
    s.store.transaction(lambda tx:tx.set('artifacts','oldartifact',{'id':'oldartifact','owner_uid':'alice','session_id':session['id'],'job_id':'oldjob','storage_mode':'drive','file_ref':'df_'+'a'*32,'sha256':'a'*64}))
    s.drive_enabled=False
    operations=[
        lambda:s.public('alice','prepare_plan',plan_body('drive'),idempotency_key='new-drive-plan'),
        lambda:s.public('alice','approve_plan',{'approval_digest':plan['approval_digest'],'confirm_execution':True},resource_id=plan['id'],idempotency_key='approve-old'),
        lambda:s.public('alice','start',{'quote_id':quote['id'],'storage_mode':'drive'},idempotency_key='new-drive-session'),
        lambda:s.public('alice','job',{'session_id':session['id'],'command':{'config':config()}},idempotency_key='new-drive-job'),
        lambda:s.public('alice','download',resource_id='oldartifact')]
    for operation in operations:
        with pytest.raises(HostedError) as error: operation()
        assert error.value.status==503
    assert s.public('alice','drive')=={'enabled':False,'connected':False}
    client=TestClient(apps.public)
    assert client.get(bs.BASE+'/artifacts/oldartifact/content',headers={'Authorization':'Bearer alice-token'}).status_code==503
    assert client.get(bs.BASE+'/artifacts/oldartifact/content',headers={'Authorization':'Bearer bob-token'}).status_code==404
    assert provider.creates==0 and s.public('alice','jobs')['jobs']==[]


def test_device_plan_runs_saves_receives_and_cleans_without_drive(apps):
    apps,provider,_=apps
    s=apps.public.state.service
    connect(s)
    plan=s.public('alice','prepare_plan',plan_body(),idempotency_key='device-plan')
    run=s.public('alice','approve_plan',{'approval_digest':plan['approval_digest'],'confirm_execution':True},resource_id=plan['id'],idempotency_key='approved')
    controller=TestClient(apps.controller)
    def reconcile():
        response=controller.post('/internal/reconcile',json={},headers=apps.internal('scheduler'))
        assert response.status_code==200,response.text
    reconcile()
    pod=next(iter(provider.pods.values()))
    assert FAKE_KEY not in json.dumps(pod)
    assert not any('DRIVE' in key or 'RUNPOD' in key for key in pod['env'])
    enrollment=s.enroll({'session_id':run['session']['id'],'token':pod['env']['FM_ENROLLMENT_TOKEN']})
    worker=dict(session_id=run['session']['id'],worker_token=enrollment['worker_token'],epoch=enrollment['epoch'])
    assert s.poll(worker)['action']=='run'
    progress=dict(phase='scanning',completed=8,total=20,progress_unit='continuations')
    s.heartbeat(dict(worker,active_job_id=run['job']['id'],seq=1,progress=progress))
    assert s.public('alice','get_job',resource_id=run['job']['id'])['progress']==progress
    payload=b'fake saved evidence'
    artifact={'id':'a'*32,'sha256':hashlib.sha256(payload).hexdigest(),'size_bytes':len(payload),'download_token':'t'*43}
    s.report(dict(worker,job_id=run['job']['id'],status='completed',artifacts=[artifact]))
    downloaded=s.public('alice','download',resource_id=artifact['id'])
    assert downloaded['sha256']==artifact['sha256'] and downloaded['size']==len(payload)
    assert s.public('alice','session',resource_id=run['session']['id'])['desired_state']=='running'
    s.public('alice','received',resource_id=artifact['id'],idempotency_key='saved')
    reconcile()
    final=s.public('alice','session',resource_id=run['session']['id'])
    assert final['observed_state']=='terminated' and final['cleanup_verified']
    assert provider.creates==1 and provider.pods=={}


def old_drive_run(apps):
    """Persist an approved Drive run as it existed before switching deployment mode."""
    public,controller=apps.public.state.service,apps.controller.state.service
    public.drive_enabled=controller.drive_enabled=True
    connect(public)
    apps.store.transaction(lambda tx:tx.set('drive_connections','alice',{'owner':'alice','status':'connected'}))
    plan=public.public('alice','prepare_plan',plan_body('drive'),idempotency_key='approved-before-disable')
    return public.public('alice','approve_plan',{'approval_digest':plan['approval_digest'],'confirm_execution':True},resource_id=plan['id'],idempotency_key='old-approval')


def test_disabling_drive_fences_approved_queue_before_create_and_enrollment(apps):
    apps,provider,now=apps
    public,controller=apps.public.state.service,apps.controller.state.service
    run=old_drive_run(apps)
    sid=run['session']['id']
    enrollment_token=public.prepare_enrollment(sid)
    public.drive_enabled=controller.drive_enabled=False
    for operation in (lambda:public.prepare_enrollment(sid),lambda:public.enroll({'session_id':sid,'token':enrollment_token})):
        with pytest.raises(HostedError) as error: operation()
        assert error.value.status==503
    # A stale controller lease must not prevent the stop intent from becoming
    # visible to a concurrent provider-create claim.
    apps.store.transaction(lambda tx:tx.set('sessions',sid,dict(tx.get('sessions',sid),claim_until=now[0]+60,claim_token='old-controller')))
    controller.reconcile()
    assert public.public('alice','session',resource_id=sid)['desired_state']=='terminated'
    assert not controller.persist_before_create(sid,{'create_attempted':True})
    now[0]+=61
    controller.reconcile()
    final=public.public('alice','session',resource_id=sid)
    assert provider.creates==0 and final['cleanup_verified'] and final['observed_state']=='terminated'
    assert public.public('alice','get_job',resource_id=run['job']['id'])['status']=='cancelled'


def test_disabling_drive_blocks_enrolled_worker_dispatch_and_cleans_existing_pod(apps):
    apps,provider,_=apps
    public,controller=apps.public.state.service,apps.controller.state.service
    run=old_drive_run(apps)
    controller.reconcile()
    assert provider.creates==1
    pod=next(iter(provider.pods.values()))
    enrollment=public.enroll({'session_id':run['session']['id'],'token':pod['env']['FM_ENROLLMENT_TOKEN']})
    worker=dict(session_id=run['session']['id'],worker_token=enrollment['worker_token'],epoch=enrollment['epoch'])
    public.drive_enabled=controller.drive_enabled=False
    response=public.poll(worker)
    assert response=={'action':'terminate'} and 'command' not in response
    assert public.public('alice','get_job',resource_id=run['job']['id'])['status']=='cancelled'
    controller.reconcile()
    final=public.public('alice','session',resource_id=run['session']['id'])
    assert final['cleanup_verified'] and final['observed_state']=='terminated'
    assert provider.creates==1 and provider.pods=={}


def test_disabled_drive_keeps_active_work_cleanup_available(apps):
    apps,provider,_=apps
    public,controller=apps.public.state.service,apps.controller.state.service
    run=old_drive_run(apps)
    controller.reconcile()
    pod=next(iter(provider.pods.values()))
    enrollment=public.enroll({'session_id':run['session']['id'],'token':pod['env']['FM_ENROLLMENT_TOKEN']})
    worker=dict(session_id=run['session']['id'],worker_token=enrollment['worker_token'],epoch=enrollment['epoch'])
    assert public.poll(worker)['action']=='run'
    public.drive_enabled=controller.drive_enabled=False
    assert public.poll(worker)=={'action':'wait','job_id':run['job']['id']}
    controller.reconcile()
    assert public.public('alice','session',resource_id=run['session']['id'])['desired_state']=='running'
    public.public('alice','terminate',resource_id=run['session']['id'],idempotency_key='finish-active')
    controller.reconcile()
    assert public.public('alice','session',resource_id=run['session']['id'])['cleanup_verified']
    assert provider.creates==1 and provider.pods=={}
