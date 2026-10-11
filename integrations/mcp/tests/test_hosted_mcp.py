"""Assembled managed MCP, fake RunPod and saved evidence. Never calls a live provider."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from fork_microscope.hosted.account import RunPodAccount, validate_runpod_key
from fork_microscope.hosted.catalog import Catalog, MODELS
from fork_microscope.hosted.lifecycle import Lifecycle
from fork_microscope.hosted.provider import FakeRunPod
from fork_microscope.hosted.service import HostedService, HostedError
from fork_microscope.hosted.store import MemoryStore
from fork_microscope.investigation_bundle import build_bundle, digest
from tools.hosted import ManagedCompute, create_hosted_mcp, build_compute_html
from tools.hosted_evidence import HostedEvidenceLibrary
from fork_microscope_hosted_mcp import artifact_reader, assemble_hosted_mcp

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tests'))
from test_evidence_io import archive


class Vault:
    def put(self, uid, kind, value): return 'test-owner-secret-'+uid
    def delete(self, ref): pass


@pytest.fixture
def managed(tmp_path):
    now = [time.time()]
    provider = FakeRunPod([{'id':'fakeGPU','manufacturer':'NVIDIA','secure':True,'memory':24,
        'price':{'secure':.5},'availability':'HIGH'}])
    provider.legacy_balance = lambda: {'amount_usd': 5, 'spend_per_hour_usd': 0}
    catalog = Catalog(lambda body: provider, disk_usd_per_gb_hour=.001, disk_rate_source='test fixture',
        worker_image='fake/image@sha256:'+'a'*64)
    service = HostedService(MemoryStore(), clock=lambda: now[0], vault=Vault(), catalog=catalog,
        credential_validator=lambda uid,key:validate_runpod_key(key,provider_builder=lambda key:provider),
        account_status=RunPodAccount(lambda body:provider).status)
    service.lifecycle = Lifecycle(lambda session:provider, worker_image='fake/image@sha256:'+'a'*64,
        control_plane_url='https://controller.example.test', persist_before_create=service.persist_before_create,
        worker_environment=lambda s:{'FM_ENROLLMENT_TOKEN':service.prepare_enrollment(s['id'])}, clock=lambda:now[0])
    blobs = {}
    def reader(uid, metadata, capability):
        assert uid == metadata['owner_uid'] if 'owner_uid' in metadata else uid == 'alice'
        return blobs[metadata['id']]
    library = HostedEvidenceLibrary(service,durable_root=tmp_path/'durable',artifact_reader=reader)
    facade = ManagedCompute(service,setup_url='https://example.test/fork-microscope/hosted.html',evidence_library=library)
    config=json.loads(Path('configs/investigation-example.json').read_text())
    config['model'].update(model_id=MODELS[0]['id'],revision=MODELS[0]['revision'])
    config['lens']=None;config['limits']['max_seconds']=100
    return facade,service,provider,now,blobs,config


def connect(service, uid='alice'):
    return service.public(uid,'runpod_put',{'api_key':'fake-only-provider-key'},idempotency_key='connect')


def approve(facade, service, config):
    connect(service)
    p=facade.prepare('alice',config,1,600,'device',True,'prepare')['plan']
    with pytest.raises(HostedError): facade.approve('alice',p['id'],p['approval_digest'],'approve',False)
    out=facade.approve('alice',p['id'],p['approval_digest'],'approve',True)
    return p,out


def test_managed_full_fixture_lifecycle_save_view_cleanup(managed,archive):
    facade,s,provider,now,blobs,cfg=managed
    assert facade.settings('alice')['account']['connected'] is False
    p,out=approve(facade,s,cfg)
    assert provider.creates==0
    assert p['quote']['worker_image']=='fake/image@sha256:'+'a'*64
    sid,jid=out['session']['id'],out['job']['id']
    assert facade.approve('alice',p['id'],p['approval_digest'],'approve',True)['session']['id']==sid
    s.reconcile();assert provider.creates==1
    pod=next(iter(provider.pods.values()))
    assert not any('RUNPOD' in k or 'DRIVE' in k for k in pod['env'])
    enrolled=s.enroll({'session_id':sid,'token':pod['env']['FM_ENROLLMENT_TOKEN']})
    worker=dict(session_id=sid,worker_token=enrolled['worker_token'],epoch=enrolled['epoch'])
    assert s.poll(worker)['action']=='run'
    assert s.poll(worker)['action']=='wait'
    s.heartbeat(dict(worker,seq=1,active_job_id=jid,progress={'phase':'scanning','completed':2,'total':20,'progress_unit':'continuations'}))
    assert facade.snapshot('alice')['job']['progress']['completed']==2
    with pytest.raises(HostedError):facade.finish('alice',sid,'finish',True)
    facade.cancel('alice',jid,'cancel')
    assert s.poll(worker)['action']=='cancel'
    artifact_id='a'*32
    raw=json.dumps(build_bundle([archive],investigation={'id':digest(jid)[:32]})).encode()
    blobs[artifact_id]=raw
    s.report(dict(worker,job_id=jid,status='cancelled',artifacts=[{'id':artifact_id,'sha256':hashlib.sha256(raw).hexdigest(),
        'size_bytes':len(raw),'download_token':'fixture-capability-never-return-to-model'}]))
    with pytest.raises(HostedError):facade.finish('alice',sid,'finish',True)
    out=facade.save('alice',artifact_id,'save')
    saved=out['saved_evidence']
    assert saved['server_saved'] and saved['receipt']['status']=='acknowledged'
    assert not out['cleanup']['verified'] and not saved['user_download_confirmed']
    from tools.evidence import fork_microscope_list_investigations
    with facade.library.profile('alice'):
        assert fork_microscope_list_investigations()['investigations'][0]['file_sha256']==hashlib.sha256(raw).hexdigest()
    with facade.library.profile('bob'):
        assert fork_microscope_list_investigations()['investigations']==[]
    provider.delete_pending=True;s.reconcile()
    assert not facade.snapshot('alice')['cleanup']['verified']
    provider.delete_pending=False;s.reconcile()
    assert facade.snapshot('alice')['cleanup']['verified'] and provider.pods=={}
    assert facade.snapshot('alice')['saved_evidence']['bundle_id']==saved['bundle_id']
    assert facade.save('alice',artifact_id,'save')['saved_evidence']['bundle_id']==saved['bundle_id']
    serialized=json.dumps(out)
    for secret in ('fake-only-provider-key','fixture-capability-never-return','test-owner-secret','worker_token'):
        assert secret not in serialized


def test_owned_status_and_finish_acknowledgement(managed):
    facade,s,provider,now,blobs,cfg=managed
    p,out=approve(facade,s,cfg);sid=out['session']['id']
    with pytest.raises(HostedError):facade.snapshot('bob',session_id=sid)
    with pytest.raises(HostedError):facade.finish('bob',sid,'finish',True,True)
    with pytest.raises(HostedError):facade.finish('alice',sid,'finish',False,True)
    done=facade.finish('alice',sid,'finish',True,True)
    assert done['cleanup']['requested'] and not done['cleanup']['verified']
    s.reconcile();assert provider.creates==0 and facade.snapshot('alice')['cleanup']['verified']


def test_managed_protocol_metadata_identity_and_confirmation(managed,monkeypatch):
    from fastmcp import Client
    from fastmcp.server.auth import AccessToken
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
    import fastmcp.server.dependencies as dependencies
    facade,s,provider,now,blobs,cfg=managed
    current=[None]
    monkeypatch.setattr(dependencies,'get_access_token',lambda:current[0])
    auth=StaticTokenVerifier(tokens={'fixture':{'client_id':'fixture','scopes':['fork:manage'],'subject':'alice'}})
    mcp=create_hosted_mcp(facade,auth=auth,owner_resolver=lambda token:token.subject)
    async def run():
        async with Client(mcp) as c:
            tools={t.name:t for t in await c.list_tools()}
            assert 'fork_microscope_start_workflow' not in tools
            assert 'app' in tools['fork_microscope_render_investigation'].meta['ui']['visibility']
            assert tools['fork_microscope_finish_compute'].annotations.destructive_hint
            for t in tools.values():
                assert not {'uid','owner_uid','api_key','worker_token'} & set(t.input_schema.get('properties',{}))
            assert (await c.call_tool('fork_microscope_compute_setup',{},raise_on_error=False)).is_error
            current[0]=AccessToken(token='fixture',client_id='fixture',scopes=['other:read'],subject='alice')
            assert (await c.call_tool('fork_microscope_compute_setup',{},raise_on_error=False)).is_error
            current[0]=AccessToken(token='fixture',client_id='fixture',scopes=['fork:manage'],subject='alice',expires_at=1)
            assert (await c.call_tool('fork_microscope_compute_setup',{},raise_on_error=False)).is_error
            current[0]=AccessToken(token='fixture',client_id='fixture',scopes=['fork:manage'],subject='alice')
            setup=(await c.call_tool('fork_microscope_compute_setup',{})).data
            assert setup['schema']=='fork-microscope-compute-v1' and setup['setup_url'].startswith('https:')
            uri=tools['fork_microscope_compute_setup'].meta['ui']['resourceUri']
            resource=(await c.read_resource(uri))[0]
            assert resource.mime_type=='text/html;profile=mcp-app' and 'ui/initialize' in resource.text
            assert '<script type="module">' in resource.text and 'src="compute.mjs"' not in resource.text
            funding=(await c.call_tool('fork_microscope_add_money',{'amount_usd':10})).data['funding']
            assert funding['payment_performed'] is False and funding['requested_amount_usd']==10
            connect(s)
            plan=(await c.call_tool('fork_microscope_prepare_plan',dict(config=cfg,usd_allowance=1,max_duration_seconds=600,
                storage_mode='device',acknowledge_device_loss=True,request_id='prepare'))).data['plan']
            args=dict(plan_id=plan['id'],approval_digest=plan['approval_digest'],request_id='approve')
            assert (await c.call_tool('fork_microscope_approve_plan',args,raise_on_error=False)).is_error
            out=(await c.call_tool('fork_microscope_approve_plan',dict(args,confirm_execution=True))).data
            assert out['job']['status']=='queued' and provider.creates==0
            current[0]=AccessToken(token='fixture-bob',client_id='fixture',scopes=['fork:manage'],subject='bob')
            assert (await c.call_tool('fork_microscope_compute_status',{'session_id':out['session']['id']},raise_on_error=False)).is_error
    asyncio.run(run())


def test_deployment_seams_fail_closed(managed,tmp_path):
    facade,*_=managed
    with pytest.raises(ValueError,match='OAuth'):create_hosted_mcp(facade,auth=None,owner_resolver=lambda t:'operator')
    with pytest.raises(ValueError,match='durable'):
        assemble_hosted_mcp(None,auth=None,owner_resolver=None,setup_url='https://example.test',durable_evidence_root=tmp_path)
    with pytest.raises(ValueError,match='HTTPS'):ManagedCompute(facade.service,setup_url='https://example.test/?key=secret',evidence_library=None)


def test_artifact_reader_confines_capability_and_closes_stream():
    closed=[];seen=[]
    class Transport:
        def __init__(self,hosts,**kwargs):assert hosts=={'pod123-8780.proxy.runpod.net'}
        def stream(self,method,url,headers,**kwargs):
            seen.append(headers)
            return SimpleNamespace(status=200,chunks=iter([b'bytes']),close=lambda:closed.append(True))
    reader=artifact_reader(None,transport_factory=Transport)
    meta={'id':'a'*32,'storage_mode':'device'}
    cap={'url':'https://pod123-8780.proxy.runpod.net/bundles/'+meta['id'],'download_token':'c'*32}
    assert b''.join(reader('alice',meta,cap))==b'bytes' and closed==[True]
    assert seen[0]['Authorization']=='Bearer '+'c'*32
    for url in ('http://localhost/private','https://evil.test/bundles/'+meta['id'],cap['url']+'?token=x'):
        with pytest.raises(ValueError):reader('alice',meta,dict(cap,url=url))


def test_original_workflow_progress_counts_retained():
    from tools.workflow import compact
    result=compact({'worker_job':{'id':'job','phase':'scanning','completed':3,'total':20,'progress_unit':'continuations'}},'secret')
    assert result['worker_job']['completed']==3 and result['worker_job']['total']==20
    assert result['worker_job']['progress_unit']=='continuations'
