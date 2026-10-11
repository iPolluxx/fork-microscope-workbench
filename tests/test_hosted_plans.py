"""Approval and lifecycle acceptance with in-memory accounts and fake compute only."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fork_microscope.hosted.api import create_app
from fork_microscope.hosted.catalog import Catalog, MODELS
from fork_microscope.hosted.lifecycle import Lifecycle
from fork_microscope.hosted.provider import FakeRunPod, ProviderError
from fork_microscope.hosted.service import HostedError, HostedService
from fork_microscope.hosted.store import MemoryStore


class Vault:
    def __init__(self): self.puts=[];self.deleted=[]
    def put(self,uid,kind,value):
        self.puts.append((uid,kind,value));return 'opaque-'+str(len(self.puts))
    def delete(self,ref): self.deleted.append(ref)


class Auth:
    def verify(self,token):
        if token not in {'alice','bob'}: raise ValueError('Invalid identity')
        return {'uid':token}


def config():
    value=json.loads(Path('configs/investigation-example.json').read_text())
    value['model'].update(model_id=MODELS[0]['id'],revision=MODELS[0]['revision'])
    value['limits']['max_seconds']=100
    value['lens']=None
    return value


@pytest.fixture
def setup():
    now=[1000.]
    provider=FakeRunPod([{'id':'fake-gpu','manufacturer':'NVIDIA','secure':True,'memory':24,'availability':'HIGH','price':{'secure':0.5}}])
    vault=Vault()
    catalog=Catalog(lambda _:provider,disk_usd_per_gb_hour=0.0001,disk_rate_source='fake test rate',worker_image='example/worker@sha256:'+'a'*64)
    service=HostedService(MemoryStore(),clock=lambda:now[0],vault=vault,catalog=catalog,
        credential_validator=lambda uid,key:{'valid':True,'status':'ready','capabilities':{'catalog_read':'verified','pods_read':'verified'}})
    service.lifecycle=Lifecycle(lambda _:provider,worker_image='example/worker@sha256:'+'a'*64,control_plane_url='https://control.example',
        persist_before_create=service.persist_before_create,worker_environment=lambda s:{'FM_ENROLLMENT_TOKEN':service.prepare_enrollment(s['id'])},clock=lambda:now[0])
    service.public('alice','runpod_put',{'api_key':'FAKE-ONLY-no-real-key'},idempotency_key='connect')
    return service,provider,vault,now


def prepare(service,*,uid='alice',key='prepare',**overrides):
    body=dict(config=config(),usd_allowance=1,max_duration_seconds=600,storage_mode='device',acknowledge_device_loss=True)
    body.update(overrides)
    return service.public(uid,'prepare_plan',body,idempotency_key=key)


def approve(service,plan,*,uid='alice',key='approve',**overrides):
    body=dict(approval_digest=plan['approval_digest'],confirm_execution=True)
    body.update(overrides)
    return service.public(uid,'approve_plan',body,resource_id=plan['id'],idempotency_key=key)


def rejected(status,fn):
    with pytest.raises(HostedError) as error: fn()
    assert error.value.status==status


def test_prepare_is_read_only_and_approval_queues_exact_config_once(setup):
    s,p,v,now=setup
    plan=prepare(s)
    assert p.creates==0 and s.public('alice','sessions')['sessions']==[]
    assert plan['status']=='prepared' and plan['config']==config()
    assert 'credential_ref' not in plan
    assert plan['quote']['max_usd']==1 and plan['expires_at']==1120
    result=approve(s,plan)
    assert result['session']['plan_id']==plan['id']
    assert result['session']['config_hash']==plan['config_hash']
    assert result['job']['status']=='queued' and p.creates==0
    internal=s.store.transaction(lambda tx:tx.get('jobs',result['job']['id']))
    assert internal['command']=={'config':plan['config']}
    assert approve(s,plan)==result
    assert approve(s,plan,key='another-approved-retry')==result
    assert len(s.public('alice','sessions')['sessions'])==len(s.public('alice','jobs')['jobs'])==1
    s.reconcile();s.reconcile()
    assert p.creates==1
    assert not any('RUNPOD' in key for key in p.pods['fake1']['env'])


def test_concurrent_duplicate_approval_creates_one_job_and_session(setup):
    s,p,_,_=setup;plan=prepare(s)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda i:approve(s,plan,key='retry-'+str(i)),range(12)))
    assert len({result['session']['id'] for result in results})==1
    assert len({result['job']['id'] for result in results})==1
    assert p.creates==0


def test_owner_digest_confirmation_and_idempotency_fences(setup):
    s,p,_,_=setup;plan=prepare(s)
    rejected(404,lambda:approve(s,plan,uid='bob'))
    rejected(404,lambda:s.public('bob','plan',resource_id=plan['id']))
    assert s.public('bob','plans')=={'plans':[]}
    rejected(409,lambda:approve(s,plan,approval_digest='0'*64))
    rejected(422,lambda:approve(s,plan,confirm_execution=False))
    rejected(422,lambda:approve(s,plan,config=config()))
    approved=approve(s,plan)
    rejected(409,lambda:approve(s,plan,confirm_execution=False))
    rejected(409,lambda:prepare(s,usd_allowance=2))
    assert len(s.public('alice','sessions')['sessions'])==1 and p.creates==0
    rejected(409,lambda:s.public('alice','job',{'session_id':approved['session']['id'],'command':{'config':config()}},idempotency_key='bypass-job'))


@pytest.mark.parametrize('field', ['config','quote'])
def test_stored_config_or_quote_tampering_invalidates_approval(setup,field):
    s,p,_,_=setup;plan=prepare(s)
    def change(tx):
        stored=tx.get('plans',plan['id'])
        if field=='config': stored['config']['base']['prompt']='Changed after review'
        else: stored['quote']['gpu_usd_per_hour']=99
        tx.set('plans',plan['id'],stored)
    s.store.transaction(change)
    rejected(409,lambda:approve(s,plan))
    assert s.public('alice','sessions')['sessions']==[] and p.creates==0


def test_plan_quote_cannot_bypass_approval_or_change_independently(setup):
    s,p,_,_=setup;plan=prepare(s)
    rejected(409,lambda:s.public('alice','start',{'quote_id':plan['quote']['id'],'storage_mode':'device','acknowledge_device_loss':True,'_approved_plan':plan['id']},idempotency_key='bypass'))
    s.store.transaction(lambda tx:tx.set('quotes',plan['quote']['id'],dict(tx.get('quotes',plan['quote']['id']),estimated_usd=99)))
    rejected(409,lambda:approve(s,plan))
    assert p.creates==0


def test_expiry_connection_change_and_reserves_fail_before_session(setup):
    s,p,_,now=setup;plan=prepare(s)
    now[0]=plan['expires_at']
    rejected(409,lambda:approve(s,plan))
    fresh=prepare(s,key='fresh')
    s.public('alice','runpod_put',{'api_key':'FAKE-ONLY-replacement'},idempotency_key='replace')
    rejected(409,lambda:approve(s,fresh,key='changed-connection'))
    excessive=config();excessive['limits']['max_seconds']=181
    rejected(422,lambda:prepare(s,key='reserve',config=excessive))
    wrong=config();wrong['model']['revision']='different-pinned-revision'
    rejected(422,lambda:prepare(s,key='model',config=wrong))
    invalid=config();invalid['scan']['samples']=False
    rejected(422,lambda:prepare(s,key='invalid',config=invalid))
    rejected(422,lambda:prepare(s,key='budget',usd_allowance=0.0001))
    assert s.public('alice','sessions')['sessions']==[] and p.creates==0


def test_queue_cancel_prevents_provisioning_and_cleanup_is_verified(setup):
    s,p,_,_=setup;run=approve(s,prepare(s))
    cancelled=s.public('alice','cancel',resource_id=run['job']['id'],idempotency_key='cancel')
    assert cancelled['status']=='cancelled'
    assert s.public('alice','session',resource_id=run['session']['id'])['desired_state']=='terminated'
    s.reconcile()
    session=s.public('alice','session',resource_id=run['session']['id'])
    assert session['cleanup_verified'] and session['observed_state']=='terminated' and p.creates==0


def test_running_cancel_progress_save_and_verified_deletion(setup):
    s,p,_,_=setup;run=approve(s,prepare(s));sid=run['session']['id'];jid=run['job']['id']
    s.reconcile()
    enrollment=s.enroll({'session_id':sid,'token':s.prepare_enrollment(sid)})
    worker={'session_id':sid,'worker_token':enrollment['worker_token'],'epoch':enrollment['epoch']}
    assert s.poll(worker)['action']=='run'
    progress={'phase':'scanning','completed':7,'total':20,'progress_unit':'continuations'}
    s.heartbeat(dict(worker,active_job_id=jid,seq=1,progress=progress))
    assert s.public('alice','get_job',resource_id=jid)['progress']==progress
    s.public('alice','cancel',resource_id=jid,idempotency_key='cancel')
    assert s.poll(worker)['action']=='cancel'
    assert s.public('alice','session',resource_id=sid)['desired_state']=='running'
    s.report(dict(worker,job_id=jid,status='cancelled',artifacts=[{'id':'saved1','sha256':'a'*64,'size_bytes':100,'download_token':'x'*32}]))
    assert s.public('alice','session',resource_id=sid)['desired_state']=='running'
    s.public('alice','received',resource_id='saved1',idempotency_key='receipt')
    p.delete_pending=True;s.reconcile()
    assert s.public('alice','session',resource_id=sid)['cleanup_verified'] is False
    p.delete_pending=False;s.reconcile()
    assert s.public('alice','session',resource_id=sid)['cleanup_verified'] is True
    assert p.creates==1 and len(p.deletes)==2


def test_uncertain_create_reconciles_without_second_post_and_deletion_failure_retries(setup):
    s,p,_,now=setup;p.uncertain_create=True;run=approve(s,prepare(s));sid=run['session']['id']
    s.reconcile();s.reconcile()
    assert p.creates==1
    s.public('alice','terminate',resource_id=sid,idempotency_key='finish')
    delete=p.delete_pod
    p.delete_pod=lambda _:(_ for _ in ()).throw(ProviderError(503))
    s.reconcile()
    failed=s.public('alice','session',resource_id=sid)
    assert failed['cleanup_verified'] is False and failed['cleanup_status']=='provider_blocked'
    p.delete_pod=delete;s.reconcile()
    assert s.public('alice','session',resource_id=sid)['cleanup_verified'] is True


def test_startup_timeout_cleans_up_even_without_browser_or_worker(setup):
    s,p,_,now=setup;run=approve(s,prepare(s));s.reconcile()
    now[0]+=301;s.reconcile()
    final=s.public('alice','session',resource_id=run['session']['id'])
    assert final['observed_state']=='terminated' and final['cleanup_verified']
    assert final['error_code']=='startup_timeout' and p.creates==1


def test_credentials_require_real_validation_before_vault_write_and_sanitize_results(setup):
    s,_,v,_=setup;puts=len(v.puts)
    s.credential_validator=None
    rejected(503,lambda:s.public('alice','runpod_put',{'api_key':'FAKE-ONLY-absent'},idempotency_key='absent'))
    s.credential_validator=lambda uid,key:{'valid':False,'status':'invalid_key','message':key}
    rejected(422,lambda:s.public('alice','runpod_put',{'api_key':'FAKE-ONLY-invalid'},idempotency_key='invalid'))
    assert len(v.puts)==puts
    assert 'mutation_claim' not in s.store.transaction(lambda tx:tx.get('connections','alice_runpod'))
    s.credential_validator=lambda uid,key:{'valid':True,'status':'ready','message':key,'api_key':key,'capabilities':{'catalog_read':'verified','pods_write':'verified','other':key}}
    result=s.public('alice','runpod_put',{'api_key':'FAKE-ONLY-safe'},idempotency_key='safe')
    connection=s.store.transaction(lambda tx:tx.get('connections','alice_runpod'))
    assert 'FAKE-ONLY-safe' not in json.dumps([result,connection])
    assert result['validation']['capabilities']['pods_write']=='unverified'


def test_expired_validation_cannot_rotate_key_pinned_by_a_new_session(setup):
    s,_,v,now=setup
    previous=s.store.transaction(lambda tx:tx.get('connections','alice_runpod'))['credential_ref']
    started=[]
    def delayed_validation(uid,key):
        now[0]+=121
        started.append(approve(s,prepare(s,key='during-validation'),key='approve-during-validation'))
        return {'valid':True,'status':'ready'}
    s.credential_validator=delayed_validation
    rejected(409,lambda:s.public('alice','runpod_put',{'api_key':'FAKE-ONLY-delayed'},idempotency_key='delayed'))
    connection=s.store.transaction(lambda tx:tx.get('connections','alice_runpod'))
    session=s.store.transaction(lambda tx:tx.get('sessions',started[0]['session']['id']))
    assert connection['credential_ref']==session['credential_ref']==previous
    assert previous not in v.deleted and v.deleted==['opaque-2']
    assert 'mutation_claim' not in connection and 'mutation_until' not in connection


def test_expired_validation_cleans_new_secret_and_can_retry(setup):
    s,_,v,now=setup
    def delayed_validation(uid,key):
        now[0]+=121
        return {'valid':True,'status':'ready'}
    s.credential_validator=delayed_validation
    rejected(409,lambda:s.public('alice','runpod_put',{'api_key':'FAKE-ONLY-delayed'},idempotency_key='retryable'))
    connection=s.store.transaction(lambda tx:tx.get('connections','alice_runpod'))
    assert connection['credential_ref']=='opaque-1' and v.deleted==['opaque-2']
    assert 'mutation_claim' not in connection
    s.credential_validator=lambda uid,key:{'valid':True,'status':'ready'}
    assert s.public('alice','runpod_put',{'api_key':'FAKE-ONLY-delayed'},idempotency_key='retryable')['connected']
    assert s.store.transaction(lambda tx:tx.get('connections','alice_runpod'))['credential_ref']=='opaque-3'
    assert v.deleted==['opaque-2','opaque-1']


def test_disconnect_commit_rechecks_lease_and_preserves_old_secret(setup):
    s,_,v,_=setup
    # The public operation reads its reservation time, then its commit time.
    s.clock=iter([1000.,1121.]).__next__
    rejected(409,lambda:s.public('alice','runpod_delete',idempotency_key='delayed-delete'))
    connection=s.store.transaction(lambda tx:tx.get('connections','alice_runpod'))
    assert connection['connected'] and connection['credential_ref']=='opaque-1'
    assert v.deleted==[] and 'mutation_claim' not in connection


def test_account_status_and_http_routes_never_accept_model_supplied_owner(setup):
    s,_,_,_=setup
    s.account_status=lambda uid:{'connected':True,'readiness':'ready','api_key':'do-not-emit','billing_url':'https://attacker.example',
        'capabilities':{'pods_write':'verified'},'balance':{'status':'available','amount_usd':4.5,'spend_per_hour_usd':0.5,'secret':'do-not-emit'}}
    c=TestClient(create_app(s,Auth()))
    headers={'Authorization':'Bearer alice','Idempotency-Key':'http-plan'}
    body=dict(config=config(),usd_allowance=1,max_duration_seconds=600,storage_mode='device',acknowledge_device_loss=True)
    assert c.post('/api/hosted/v1/plans',json=body).status_code==401
    plan=c.post('/api/hosted/v1/plans',json=body,headers=headers).json()
    assert c.get('/api/hosted/v1/plans/'+plan['id'],headers={'Authorization':'Bearer bob'}).status_code==404
    assert c.post('/api/hosted/v1/plans',json=dict(body,owner_uid='bob'),headers=dict(headers,**{'Idempotency-Key':'bad-owner'})).status_code==422
    account=c.get('/api/hosted/v1/account',headers=headers)
    assert account.status_code==200 and 'do-not-emit' not in account.text
    assert account.json()['billing_url']=='https://console.runpod.io/billing'
    assert account.json()['write_permissions_verified'] is False
    assert account.json()['balance']['amount_usd']==4.5
    approved=c.post('/api/hosted/v1/plans/'+plan['id']+'/approve',json={'approval_digest':plan['approval_digest'],'confirm_execution':True},headers=dict(headers,**{'Idempotency-Key':'http-approve'}))
    assert approved.status_code==200 and approved.json()['job']['status']=='queued'


def worker_identity(service,sid):
    token=service.prepare_enrollment(sid)
    enrolled=service.enroll({'session_id':sid,'token':token})
    return dict(session_id=sid,worker_token=enrolled['worker_token'],epoch=enrolled['epoch'])


def test_controller_verified_cleanup_closes_running_job_and_fences_late_worker(setup):
    s,p,_,now=setup;run=approve(s,prepare(s));sid=run['session']['id'];jid=run['job']['id']
    s.reconcile();worker=worker_identity(s,sid);s.poll(worker)
    s.heartbeat(dict(worker,seq=1,active_job_id=jid))
    s.public('alice','terminate',resource_id=sid,idempotency_key='finish-with-loss-approved')
    s.reconcile()
    session=s.public('alice','session',resource_id=sid)
    job=s.public('alice','get_job',resource_id=jid)
    assert session['cleanup_verified'] is True and session['observed_state']=='terminated'
    assert job['status']=='interrupted' and job['finished_at']==now[0]
    assert 'command' not in s.store.transaction(lambda tx:tx.get('jobs',jid))
    rejected(401,lambda:s.heartbeat(dict(worker,seq=2,active_job_id=jid)))
    rejected(401,lambda:s.report(dict(worker,job_id=jid,status='failed')))
    s.reconcile()
    assert s.public('alice','get_job',resource_id=jid)==job and len(p.deletes)==1


def test_provider_startup_deadline_closes_queued_job_in_same_cleanup_tick(setup):
    s,p,_,now=setup;run=approve(s,prepare(s));s.reconcile();now[0]+=301
    s.reconcile()
    session=s.public('alice','session',resource_id=run['session']['id'])
    job=s.public('alice','get_job',resource_id=run['job']['id'])
    assert session['cleanup_verified'] and session['error_code']=='startup_timeout'
    assert job['status']=='cancelled' and job['finished_at']==now[0] and job['error_code']=='startup_timeout'
    assert p.creates==1


def test_crash_after_create_before_provider_id_persistence_recovers_by_name(setup):
    s,p,_,_=setup;run=approve(s,prepare(s));create=p.create_pod
    def create_then_crash(body):
        create(body)
        raise RuntimeError('simulated process interruption after successful POST')
    p.create_pod=create_then_crash
    s.reconcile()
    stranded=s.store.transaction(lambda tx:tx.get('sessions',run['session']['id']))
    assert stranded['create_attempted'] and 'provider_ref' not in stranded and p.creates==1
    p.create_pod=create;s.reconcile()
    recovered=s.public('alice','session',resource_id=run['session']['id'])
    assert recovered['provider_ref']=='fake1' and p.creates==1
    s.public('alice','cancel',resource_id=run['job']['id'],idempotency_key='cancel-after-recovery')
    s.reconcile()
    assert s.public('alice','session',resource_id=run['session']['id'])['cleanup_verified'] and not p.pods


def test_cancel_during_create_keeps_termination_intent_and_deletes_late_pod(setup):
    s,p,_,_=setup;run=approve(s,prepare(s));create=p.create_pod
    def delayed_create(body):
        pod=create(body)
        s.public('alice','cancel',resource_id=run['job']['id'],idempotency_key='cancel-during-create')
        return pod
    p.create_pod=delayed_create;s.reconcile()
    mid=s.public('alice','session',resource_id=run['session']['id'])
    assert mid['desired_state']=='terminated' and p.creates==1
    rejected(409,lambda:s.prepare_enrollment(run['session']['id']))
    s.reconcile()
    final=s.public('alice','session',resource_id=run['session']['id'])
    assert final['cleanup_verified'] and final['observed_state']=='terminated' and not p.pods
    assert s.public('alice','get_job',resource_id=run['job']['id'])['status']=='cancelled'


def test_worker_enrollment_during_create_response_is_not_regressed_to_booting(setup):
    s,p,_,_=setup;run=approve(s,prepare(s));create=p.create_pod
    def enroll_before_return(body):
        pod=create(body)
        enrolled=s.enroll({'session_id':run['session']['id'],'token':body['env']['FM_ENROLLMENT_TOKEN']})
        worker=dict(session_id=run['session']['id'],worker_token=enrolled['worker_token'],epoch=enrolled['epoch'])
        assert s.poll(worker)['action']=='run'
        return pod
    p.create_pod=enroll_before_return;s.reconcile()
    session=s.public('alice','session',resource_id=run['session']['id'])
    assert session['observed_state']=='running' and session['provider_ref']=='fake1'
    rejected(409,lambda:s.prepare_enrollment(session['id']))


def test_terminal_report_during_provider_read_is_not_resurrected_as_running(setup):
    s,p,_,_=setup;run=approve(s,prepare(s));sid=run['session']['id'];jid=run['job']['id']
    s.reconcile();worker=worker_identity(s,sid);s.poll(worker);s.heartbeat(dict(worker,seq=1,active_job_id=jid))
    listing=p.list_pods;reported=[False]
    def report_during_read():
        value=listing()
        if not reported[0]:
            reported[0]=True
            s.report(dict(worker,job_id=jid,status='completed',artifacts=[dict(id='savedresult',sha256='a'*64,size_bytes=100,download_token='x'*32)]))
        return value
    p.list_pods=report_during_read;s.reconcile()
    assert s.public('alice','get_job',resource_id=jid)['status']=='completed'
    assert s.public('alice','session',resource_id=sid)['observed_state']=='ready'


def test_late_dispatch_preserves_exact_execution_and_export_allowances(setup):
    s,p,_,now=setup;run=approve(s,prepare(s));sid=run['session']['id']
    now[0]=run['session']['expires_at']-run['plan']['quote']['export_reserve_seconds']-config()['limits']['max_seconds']+1
    worker=worker_identity(s,sid)
    assert s.poll(worker)=={'action':'terminate'}
    job=s.public('alice','get_job',resource_id=run['job']['id'])
    assert job['status']=='cancelled' and job['error_code']=='insufficient_execution_time'
    assert s.public('alice','session',resource_id=sid)['desired_state']=='terminated' and p.creates==0


def test_terminal_report_retries_are_exact_across_service_instances(setup):
    s,_,_,now=setup;run=approve(s,prepare(s));worker=worker_identity(s,run['session']['id']);s.poll(worker)
    artifact=dict(id='savedresult',sha256='a'*64,size_bytes=100,download_token='x'*32)
    report=dict(worker,job_id=run['job']['id'],status='completed',artifacts=[artifact])
    result=s.report(report)
    fresh=HostedService(s.store,clock=lambda:now[0])
    assert fresh.report(report)==result
    for altered in (dict(artifact,sha256='b'*64),dict(artifact,size_bytes=101),dict(artifact,download_token='y'*32)):
        rejected(409,lambda:fresh.report(dict(report,artifacts=[altered])))
    assert len(fresh.public('alice','artifacts')['artifacts'])==1
    assert fresh.public('alice','artifacts')['artifacts'][0]['sha256']=='a'*64


@pytest.mark.parametrize('bad', [None, {}, ['not-an-artifact'], [dict(id='bad/path',sha256='a'*64,size_bytes=100)],
    [dict(id='valid',sha256=None,size_bytes=100)], [dict(id='valid',sha256='a'*64,size_bytes=100.5)]])
def test_malformed_terminal_metadata_fails_without_artifacts_or_state_changes(setup,bad):
    s,_,_,_=setup;run=approve(s,prepare(s));worker=worker_identity(s,run['session']['id']);s.poll(worker)
    rejected(422,lambda:s.report(dict(worker,job_id=run['job']['id'],status='completed',artifacts=bad)))
    assert s.public('alice','artifacts')['artifacts']==[]
    assert s.public('alice','get_job',resource_id=run['job']['id'])['status']=='running'


def test_malformed_approval_digest_returns_actionable_validation_error(setup):
    s,p,_,_=setup;plan=prepare(s)
    for value in ('é'*64,'A'*64,'a'*63):
        rejected(422,lambda:approve(s,plan,key='digest-'+value,approval_digest=value))
    assert p.creates==0 and s.public('alice','sessions')['sessions']==[]


def test_drive_verification_normalization_does_not_mutate_exact_retry_request(setup):
    s,_,_,_=setup
    s.store.transaction(lambda tx:tx.set('drive_connections','alice',{'owner':'alice','status':'connected'}))
    run=approve(s,prepare(s,storage_mode='drive',acknowledge_device_loss=False));worker=worker_identity(s,run['session']['id']);s.poll(worker)
    calls=[]
    def normalize(session,job,artifacts):
        calls.append(True);artifacts[0]['file_ref']='verified-opaque-reference'
    s.artifact_verify=normalize
    artifact=dict(id='driveartifact',sha256='a'*64,size_bytes=100,file_ref='worker-reference')
    report=dict(worker,job_id=run['job']['id'],status='completed',artifacts=[artifact])
    result=s.report(report)
    assert artifact['file_ref']=='worker-reference'
    assert s.report(report)==result and calls==[True]
    saved=s.public('alice','artifacts')['artifacts'][0]
    assert saved['file_ref']=='verified-opaque-reference'


def test_late_heartbeat_after_terminal_report_keeps_saved_job_unchanged(setup):
    s,_,_,_=setup;run=approve(s,prepare(s));sid=run['session']['id'];jid=run['job']['id']
    worker=worker_identity(s,sid);s.poll(worker)
    s.heartbeat(dict(worker,seq=1,active_job_id=jid,progress={'phase':'scanning','completed':7,'total':20}))
    s.report(dict(worker,job_id=jid,status='completed',artifacts=[dict(id='savedresult',sha256='a'*64,size_bytes=100,download_token='x'*32)]))
    saved=json.dumps(s.store.transaction(lambda tx:tx.get('jobs',jid)),sort_keys=True)
    s.heartbeat(dict(worker,seq=2,active_job_id=jid,progress={'phase':'saving','completed':0,'total':0}))
    assert json.dumps(s.store.transaction(lambda tx:tx.get('jobs',jid)),sort_keys=True)==saved


def test_worker_report_during_pending_deletion_does_not_reopen_session(setup):
    s,p,_,_=setup;run=approve(s,prepare(s));sid=run['session']['id'];jid=run['job']['id']
    s.reconcile();worker=worker_identity(s,sid);s.poll(worker);s.heartbeat(dict(worker,seq=1,active_job_id=jid))
    s.public('alice','terminate',resource_id=sid,idempotency_key='stop')
    p.delete_pending=True;s.reconcile()
    assert s.public('alice','session',resource_id=sid)['observed_state']=='terminating'
    s.report(dict(worker,job_id=jid,status='saving'))
    assert s.public('alice','session',resource_id=sid)['observed_state']=='terminating'
    s.report(dict(worker,job_id=jid,status='failed'))
    session=s.public('alice','session',resource_id=sid)
    assert session['observed_state']=='terminating' and session['desired_state']=='terminated' and not session['cleanup_verified']
    p.delete_pending=False;s.reconcile()
    final=s.public('alice','session',resource_id=sid)
    assert final['observed_state']=='terminated' and final['cleanup_verified'] is True and p.creates==1


def test_mismatched_provider_reference_cleans_exact_name_without_verifying_absence(setup):
    s,p,_,_=setup;run=approve(s,prepare(s));sid=run['session']['id']
    s.reconcile()
    assert s.public('alice','session',resource_id=sid)['provider_ref']=='fake1'
    p.pods['fake1']['name']='renamed-outside-control-plane'
    p.pods['late']={'id':'late','name':'fm-'+sid,'status':'RUNNING'}
    for _ in range(2):
        s.reconcile()
        session=s.public('alice','session',resource_id=sid)
        assert session['desired_state']=='terminated' and session['error_code']=='provider_ownership_mismatch'
        assert session['cleanup_verified'] is False and session['manual_attention_required'] is True
        assert session['provider_ref']=='fake1' and 'fake1' in p.pods
    assert p.deletes==['late'] and p.creates==1
    assert s.public('alice','get_job',resource_id=run['job']['id'])['status']=='cancelled'
    fresh=prepare(s,key='second')
    rejected(409,lambda:approve(s,fresh,key='second'))


def test_cancel_while_completed_result_is_saving_keeps_the_result_and_awaits_receipt(setup):
    s,_,_,_=setup;run=approve(s,prepare(s));worker=worker_identity(s,run['session']['id']);s.poll(worker)
    s.report(dict(worker,job_id=run['job']['id'],status='saving'))
    s.public('alice','cancel',resource_id=run['job']['id'],idempotency_key='cancel-during-save')
    artifact=dict(id='alreadycompleted',sha256='a'*64,size_bytes=100,download_token='x'*32)
    s.report(dict(worker,job_id=run['job']['id'],status='completed',artifacts=[artifact]))
    assert s.public('alice','get_job',resource_id=run['job']['id'])['status']=='completed'
    assert s.public('alice','session',resource_id=run['session']['id'])['desired_state']=='running'
    assert s.public('alice','artifacts')['artifacts'][0]['id']=='alreadycompleted'
    s.public('alice','received',resource_id='alreadycompleted',idempotency_key='receipt')
    assert s.public('alice','session',resource_id=run['session']['id'])['desired_state']=='terminated'
