"""Offline imports: data, isolated stores and fake auth only. Never uploads user evidence."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pickle
import subprocess
import sys
import threading

import httpx
import pytest
from fastapi import FastAPI
from fork_microscope.hosted.service import HostedService, HostedError
from fork_microscope.hosted.store import MemoryStore
from fork_microscope.investigation_bundle import build_bundle
from pilot_evidence import PilotEvidenceLibrary, RETENTION_SECONDS, LEASE_SECONDS, QUOTA_BYTES
from pilot_budget import PilotBudget, BudgetedService, COLLECTION
from pilot_cloud import mount_import_api

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'integrations/mcp/tests'))
from offline_support import adapter_context
from test_evidence_io import archive


@pytest.fixture
def imported(archive):
    now=[1791504000.]
    store=MemoryStore();service=HostedService(store,clock=lambda:now[0])
    def forbidden(*_):raise AssertionError('Import must not fetch, execute or acknowledge compute')
    def delete(keys):
        with store._lock:
            for key in keys:store._data.get('evidence_chunks',{}).pop(key,None)
    library=PilotEvidenceLibrary(service,store=store,pilot_uid='alice',artifact_reader=forbidden,
                                 delete_chunks=delete,clock=lambda:now[0])
    raw=json.dumps(build_bundle([archive])).encode()
    return library,store,service,now,raw


def put(library,raw,request='import-1',synthetic=True,uid='alice'):
    return library.import_bytes(uid,raw,hashlib.sha256(raw).hexdigest(),request,synthetic=synthetic)


def test_roundtrip_dedup_and_new_library_without_device_or_model(imported):
    library,store,service,now,raw=imported
    with adapter_context():
        out=put(library,raw)
        again=put(library,raw,'different-retry')
        assert again['bundle_id']==out['bundle_id'] and again['deduplicated']
        assert out['provenance']=={'source':'user_file','synthetic':True,'execution_verified':False}
        assert out['receipt']=={'status':'not_required','cleanup_requested':None}
        assert not out['cleanup_applicable'] and not out['cleanup_verified']
        restarted=PilotEvidenceLibrary(service,store=store,pilot_uid='alice',artifact_reader=library.reader,
                                      delete_chunks=library.delete_chunks,clock=lambda:now[0])
        assert restarted.list_investigations('alice')['total_candidates']==1
        with restarted.profile_for('alice',out['bundle_id']) as profile:
            from tools import evidence,viewer
            args={'bundle_id':out['bundle_id'],'expected_file_sha256':out['file_sha256']}
            assert evidence.fork_microscope_inspect_investigation(**args)['bundle']['provenance']==out['provenance']
            assert evidence.fork_microscope_report_investigation(**args)['bundle']['provenance']==out['provenance']
            assert viewer.fork_microscope_render_investigation(**args)['bundle']['provenance']==out['provenance']
            exported=evidence.fork_microscope_export_investigation(out['bundle_id'])
            assert Path(exported['artifacts'][0]['path']).read_bytes()==raw
            chunks=[];offset=0
            while True:
                chunk=evidence.fork_microscope_read_investigation_bytes(**args,offset=offset,length=4096)
                assert chunk['bundle']['provenance']==out['provenance']
                chunks.append(base64.b64decode(chunk['data']))
                if chunk['next_offset'] is None:break
                offset=chunk['next_offset']
            assert b''.join(chunks)==raw
        assert put(restarted,raw,'reimport')['deduplicated']
    assert not set(store._data)&{'artifacts','jobs','sessions','connections'}
    catalog=store.transaction(lambda tx:tx.get('evidence_catalog',library.owner_key))
    assert catalog['bytes']==len(raw) and len(catalog['items'])==1


@pytest.mark.parametrize('variation',['sha','schema','duplicate','nonfinite','depth','path','owner','label'])
def test_rejected_import_never_reserves_or_fetches(imported,variation):
    library,store,_,_,raw=imported;uid='alice';request='one';synthetic=True;checksum=hashlib.sha256(raw).hexdigest()
    if variation=='sha':checksum='0'*64
    elif variation=='schema':raw=b'{"schema":"not-native","url":"https://secret.invalid/"}'
    elif variation=='duplicate':raw=b'{"schema":1,"schema":2}'
    elif variation=='nonfinite':raw=b'{"x":NaN}'
    elif variation=='depth':raw=b'['*65+b']'*65
    elif variation=='path':request='../../secret'
    elif variation=='owner':uid='bob'
    elif variation=='label':synthetic='false'
    if variation not in ('sha','path','owner','label'):checksum=hashlib.sha256(raw).hexdigest()
    with pytest.raises(HostedError):library.import_bytes(uid,raw,checksum,request,synthetic=synthetic)
    assert store._data=={}


def test_idempotency_bound_to_file_and_label_and_retention_not_extended(imported):
    library,store,_,now,raw=imported
    with adapter_context():
        saved=put(library,raw)
        key,manifest=library._manifest('alice',saved['artifact_id'])
        now[0]+=5
        assert put(library,raw)['deduplicated']
        assert library._manifest('alice',saved['artifact_id'])[1]['expires_at']==manifest['expires_at']
        with pytest.raises(HostedError,match='provenance|label'):put(library,raw,'label-change',False)
        changed=raw+b' '
        with pytest.raises(HostedError,match='different'):put(library,changed)


def test_partial_corrupt_readback_retry_and_lost_commit_reply(imported):
    library,store,_,_,raw=imported
    original=library._read
    library._read=lambda *_:(_ for _ in ()).throw(ValueError('corrupt'))
    with adapter_context():
        with pytest.raises(HostedError,match='incomplete'):put(library,raw)
        assert library.list_investigations('alice')['total_candidates']==0
        library._read=original
        tx=store.transaction;lost=[False]
        def uncertain(fn):
            result=tx(fn)
            if isinstance(result,dict) and result.get('state')=='committed' and not lost[0]:
                lost[0]=True;raise TimeoutError('reply lost')
            return result
        store.transaction=uncertain
        assert put(library,raw)['saved'] and lost[0]
        assert put(library,raw)['deduplicated']


def test_reload_with_new_request_resumes_same_owned_generation(imported):
    library,store,_,_,raw=imported
    original=library._read
    library._read=lambda *_:(_ for _ in ()).throw(ValueError('response lost'))
    with adapter_context():
        with pytest.raises(HostedError):put(library,raw,'before-reload')
        key=next(iter(store._data['evidence_manifests']))
        before=store.transaction(lambda tx:tx.get('evidence_manifests',key))
        library._read=original
        saved=put(library,raw,'after-reload')
        after=store.transaction(lambda tx:tx.get('evidence_manifests',key))
        assert saved['saved'] and after['generation']==before['generation']
        assert after['expires_at']==before['expires_at'] and after['lease_until']==before['lease_until']
        catalog=store.transaction(lambda tx:tx.get('evidence_catalog',library.owner_key))
        assert catalog['bytes']==len(raw) and len(catalog['items'])==1


def test_concurrent_same_file_retry_has_one_reservation(imported):
    library,store,service,now,raw=imported
    other=PilotEvidenceLibrary(service,store=store,pilot_uid='alice',artifact_reader=library.reader,
                              delete_chunks=library.delete_chunks,clock=lambda:now[0])
    barrier=threading.Barrier(2)
    for lib in (library,other):
        original=lib._read
        def overlap(key,manifest,original=original):
            if manifest['state']=='uploading':barrier.wait(timeout=5)
            return original(key,manifest)
        lib._read=overlap
    with adapter_context(),ThreadPoolExecutor(max_workers=2) as executor:
        futures=[executor.submit(put,lib,raw,'same-request') for lib in (library,other)]
        results=[future.result() for future in futures]
    assert all(result['saved'] for result in results)
    assert results[0]['bundle_id']==results[1]['bundle_id']
    catalog=store.transaction(lambda tx:tx.get('evidence_catalog',library.owner_key))
    assert catalog['bytes']==len(raw) and len(catalog['items'])==1


def test_atomic_quota_race_and_size_rejection(imported):
    library,store,service,now,raw=imported
    store.transaction(lambda tx:tx.set('evidence_catalog',library.owner_key,{'bytes':QUOTA_BYTES-len(raw),'items':{}}))
    other=PilotEvidenceLibrary(service,store=store,pilot_uid='alice',artifact_reader=library.reader,
                              delete_chunks=library.delete_chunks,clock=lambda:now[0])
    with adapter_context(),ThreadPoolExecutor(max_workers=2) as executor:
        futures=[executor.submit(put,lib,body,rid) for lib,body,rid in
                 ((library,raw,'race-1'),(other,raw+b' ','race-2'))]
        results=[]
        for future in futures:
            try:results.append(future.result())
            except HostedError as error:assert error.status==409
        assert len(results)==1
    catalog=store.transaction(lambda tx:tx.get('evidence_catalog',library.owner_key))
    assert catalog['bytes']<=QUOTA_BYTES and len(catalog['items'])==1
    library.max_artifact=1
    with pytest.raises(HostedError) as error:put(library,raw,'oversize')
    assert error.value.status==413


def test_retention_partial_cleanup_and_ownership_cache_isolation(imported):
    library,store,service,now,raw=imported
    with adapter_context():
        saved=put(library,raw)
        with pytest.raises(HostedError),library.profile_for('bob',saved['bundle_id']):pass
        now[0]+=RETENTION_SECONDS+1
        with pytest.raises(HostedError),library.profile_for('alice',saved['bundle_id']):pass
        library.cleanup_expired()
        with pytest.raises(HostedError) as error:put(library,raw)
        assert error.value.status==410
        restored=put(library,raw,'explicit-new-import')
        assert restored['saved']
        library._read=lambda *_:(_ for _ in ()).throw(ValueError('readback failed'))
        with pytest.raises(HostedError):put(library,raw+b' ','partial')
        now[0]+=LEASE_SECONDS+1
        assert library.cleanup_expired()['removed_pilot_artifacts']==1


def test_http_auth_before_body_and_file_contract(imported):
    library,store,service,now,raw=imported
    budget=PilotBudget(store,pilot_uid='alice',clock=lambda:now[0])
    app=FastAPI();app.state.service=BudgetedService(service,budget)
    class Auth:
        def verify(self,token):return {'uid':token}
    mount_import_api(app,library=library,authenticator=Auth(),issuer='https://pilot.example.test')
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://pilot.example.test') as client:
            headers={'Authorization':'Bearer alice','Idempotency-Key':'http-1','X-Fork-Evidence-SHA256':hashlib.sha256(raw).hexdigest(),'Content-Type':'application/json'}
            with adapter_context():
                response=await client.post('/api/hosted/v1/evidence/import',content=raw,headers=headers)
                assert response.status_code==200,response.text
            assert (await client.post('/api/hosted/v1/evidence/import',content=raw,headers={**headers,'Origin':'https://evil.invalid'})).status_code==403
            assert (await client.post('/api/hosted/v1/evidence/import',content=raw,headers={**headers,'Authorization':'Bearer bob'})).status_code==401
            assert (await client.post('/api/hosted/v1/evidence/import',content=raw,headers={**headers,'Content-Type':'text/plain'})).status_code==415
            assert (await client.post('/api/hosted/v1/evidence/import',content=raw,headers={**headers,'Idempotency-Key':'../../bad'})).status_code==422
            assert (await client.post('/api/hosted/v1/evidence/import',content=raw,headers={**headers,'Content-Encoding':'gzip'})).status_code==415
            library.max_artifact=len(raw)-1
            assert (await client.post('/api/hosted/v1/evidence/import',content=raw,headers=headers)).status_code==413
            library.max_artifact=len(raw)
            assert (await client.get('/api/hosted/v1/evidence',headers={'Authorization':'Bearer alice'})).json()['total_candidates']==1
        async def receive():raise AssertionError('Unauthenticated body was consumed')
        messages=[]
        async def send(event):messages.append(event)
        await app({'type':'http','http_version':'1.1','method':'POST','scheme':'https','path':'/api/hosted/v1/evidence/import','raw_path':b'/api/hosted/v1/evidence/import','query_string':b'','root_path':'','headers':[],'server':('pilot.example.test',443),'client':None},receive,send)
        assert messages[0]['status']==401
    asyncio.run(run())


def test_import_obeys_existing_hosting_admission_stop_with_read_recovery(imported):
    library,store,service,now,raw=imported
    budget=PilotBudget(store,pilot_uid='alice',clock=lambda:now[0]);library.admission=budget.admit
    month=datetime.fromtimestamp(now[0],timezone.utc).strftime('%Y-%m')
    store.transaction(lambda tx:tx.set(COLLECTION,'pilot_budget_control',dict(
        enabled=True,observed_at=now[0],estimated_total_usd=1,month=month)))
    with adapter_context():saved=put(library,raw)
    store.transaction(lambda tx:tx.set(COLLECTION,'pilot_budget_control',{'enabled':False}))
    with adapter_context():
        with pytest.raises(HostedError,match='paused'):put(library,raw+b' ','new-file')
        with pytest.raises(HostedError,match='paused'):put(library,raw,'new-dedup-request')
        with budget.operation('recovery'):
            assert library.list_investigations('alice')['total_candidates']==1
            with library.profile_for('alice',saved['bundle_id']) as profile:
                assert (profile['evidence_dir']/saved['bundle_id']).read_bytes()==raw
    assert len(store._data['evidence_manifests'])==1


@pytest.mark.parametrize('variable,synthetic',[('FORK_IMPORT_SYNTHETIC_FIXTURE',True),('FORK_IMPORT_REAL_FIXTURE',False)])
def test_existing_saved_fixture_exact_export_and_fresh_process(imported,tmp_path,monkeypatch,variable,synthetic):
    if not os.environ.get(variable):pytest.skip('Optional local fixture path not supplied')
    library,store,service,now,_=imported
    raw=Path(os.environ[variable]).read_bytes()
    with adapter_context():saved=put(library,raw,'recorded-fixture',synthetic)
    from fastmcp import Client
    from fastmcp.server.auth import AccessToken
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
    import fastmcp.server.dependencies as dependencies
    from tools.hosted import ManagedCompute, create_hosted_mcp
    current=[AccessToken(token='fixture',client_id='fixture',scopes=['fork:manage'],subject='alice')]
    monkeypatch.setattr(dependencies,'get_access_token',lambda:current[0])
    auth=StaticTokenVerifier(tokens={'fixture':{'client_id':'fixture','scopes':['fork:manage'],'subject':'alice'}})
    mcp=create_hosted_mcp(ManagedCompute(service,setup_url='https://pilot.example.test/hosted.html',evidence_library=library),
                          auth=auth,owner_resolver=lambda token:token.subject)
    async def protocol():
        async with Client(mcp) as client:
            listing=(await client.call_tool('fork_microscope_list_investigations',{})).data
            assert listing['total_candidates']==1
            handoff=(await client.call_tool('fork_microscope_import_investigation',{})).data
            assert handoff['import']['url']=='https://pilot.example.test/hosted.html#saved-import'
            args={'bundle_id':saved['bundle_id'],'expected_file_sha256':saved['file_sha256']}
            for name in ('fork_microscope_inspect_investigation','fork_microscope_report_investigation',
                         'fork_microscope_render_investigation','fork_microscope_get_investigation_view'):
                result=(await client.call_tool(name,args)).data
                assert result['bundle']['provenance']==saved['provenance']
                assert result['bundle']['file_sha256']==saved['file_sha256']
            exported=[];offset=0
            while True:
                chunk=(await client.call_tool('fork_microscope_read_investigation_bytes',dict(args,offset=offset,length=49152))).data
                exported.append(base64.b64decode(chunk['data']))
                if chunk['next_offset'] is None:break
                offset=chunk['next_offset']
            assert b''.join(exported)==raw
            assert put(library,b''.join(exported),'protocol-reimport',synthetic)['deduplicated']
            current[0]=AccessToken(token='fixture-bob',client_id='fixture',scopes=['fork:manage'],subject='bob')
            assert (await client.call_tool('fork_microscope_inspect_investigation',args,raise_on_error=False)).is_error
    asyncio.run(protocol())
    # Trusted test persistence only. The child has no access to the fixture source.
    state=tmp_path/'store.pkl';state.write_bytes(pickle.dumps(store._data))
    script='''import sys,pickle,hashlib,importlib.abc
class NoModels(importlib.abc.MetaPathFinder):
 def find_spec(self,name,path=None,target=None):
  if name.split('.')[0] in ('torch','transformers','forking_paths'):raise AssertionError('model import')
sys.meta_path.insert(0,NoModels())
from fork_microscope.hosted.store import MemoryStore
from fork_microscope.hosted.service import HostedService
from pilot_evidence import PilotEvidenceLibrary
from offline_support import adapter_context
store=MemoryStore();store._data=pickle.load(open(sys.argv[1],'rb'))
def forbidden(*_):raise AssertionError('source dependence')
library=PilotEvidenceLibrary(HostedService(store),store=store,pilot_uid='alice',artifact_reader=forbidden,delete_chunks=forbidden,clock=lambda:1791504000.)
with adapter_context(),library.profile_for('alice',sys.argv[2]) as profile:
 raw=(profile['evidence_dir']/sys.argv[2]).read_bytes()
 assert hashlib.sha256(raw).hexdigest()==sys.argv[3]
 print('fresh_process_exact_bytes='+str(len(raw)))
'''
    environment={**os.environ,'FORK_IMPORT_REAL_FIXTURE':'','FORK_IMPORT_SYNTHETIC_FIXTURE':'','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'}
    result=subprocess.run([sys.executable,'-c',script,str(state),saved['bundle_id'],saved['file_sha256']],env=environment,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert str(len(raw)) in result.stdout
    with adapter_context():assert put(library,raw,'reimport-fixture',synthetic)['deduplicated']
