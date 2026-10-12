"""Offline evidence acceptance: no provider, Firebase, Firestore or model calls."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'integrations/mcp/tests'))
from offline_support import adapter_context
from test_evidence_io import archive
from fork_microscope.hosted.service import HostedService, HostedError
from fork_microscope.hosted.store import MemoryStore
from fork_microscope.investigation_bundle import build_bundle, digest
from pilot_evidence import PilotEvidenceLibrary, RETENTION_SECONDS, LEASE_SECONDS, QUOTA_BYTES


@pytest.fixture
def cloud_evidence(archive):
    now=[100.0]
    uid, aid, jid, sid='alice', 'a'*32, 'b'*32, 'c'*32
    raw=json.dumps(build_bundle([archive], investigation={'id':digest(jid)[:32]})).encode()
    store=MemoryStore(); service=HostedService(store, clock=lambda:now[0])
    meta=dict(id=aid,owner_uid=uid,job_id=jid,session_id=sid,storage_mode='device',
              size=len(raw),sha256=hashlib.sha256(raw).hexdigest(),download_token='fake-secret-capability')
    def seed(tx):
        tx.set('artifacts',aid,meta)
        tx.set('jobs',jid,dict(id=jid,owner_uid=uid,session_id=sid,status='completed'))
        tx.set('sessions',sid,dict(id=sid,owner_uid=uid,expires_at=5000,observed_state='ready',desired_state='running',provider_ref='testpod'))
    store.transaction(seed)
    calls=[]
    def reader(*_):calls.append('download');return raw
    def delete(keys):
        with store._lock:
            for key in keys:store._data.get('evidence_chunks',{}).pop(key,None)
    library=PilotEvidenceLibrary(service,store=store,pilot_uid=uid,artifact_reader=reader,
                                 delete_chunks=delete,clock=lambda:now[0])
    return library,store,service,now,meta,raw,calls


def save(library, meta):
    with adapter_context():return library.save('alice',meta['id'],'save-1')


def test_complexity_preflight_bounds_expansion_and_handles_quoted_syntax():
    from pilot_evidence import check_json_complexity, MAX_JSON_STRUCTURE, MAX_JSON_DEPTH
    check_json_complexity(json.dumps({'text':'[{},]"\\'*MAX_JSON_STRUCTURE}).encode())
    with pytest.raises(ValueError,match='complex'):
        check_json_complexity(b'['*(MAX_JSON_DEPTH+1)+b']'*(MAX_JSON_DEPTH+1))
    with pytest.raises(ValueError,match='complex'):
        check_json_complexity(b'['+b'0,'*MAX_JSON_STRUCTURE+b'0]')


def test_fresh_instance_retrieves_exact_bytes_after_source_is_gone(cloud_evidence):
    library,store,service,now,meta,raw,calls=cloud_evidence
    result=save(library,meta)
    assert result['server_saved'] and result['receipt']['status']=='acknowledged'
    key,m=library._manifest('alice',meta['id'])
    assert m['state']=='committed' and m['expires_at']==100+RETENTION_SECONDS
    restarted=PilotEvidenceLibrary(service,store=store,pilot_uid='alice',
        artifact_reader=lambda *_:pytest.fail('Deleted source must not be fetched'),
        delete_chunks=library.delete_chunks,clock=lambda:now[0])
    assert save(restarted,meta)==result
    with restarted.profile_for('alice',result['bundle_id']) as p:
        assert (p['evidence_dir']/result['bundle_id']).read_bytes()==raw
        directory=p['evidence_dir']
    assert not directory.exists()
    assert restarted.list_investigations('alice')['investigations'][0]['file_sha256']==meta['sha256']
    assert calls==['download']


def test_receipt_requires_independent_verified_readback(cloud_evidence):
    library,store,service,now,meta,raw,calls=cloud_evidence
    original=library._read
    library._read=lambda *_:(_ for _ in ()).throw(ValueError('Corrupt independent readback'))
    with pytest.raises(ValueError):save(library,meta)
    assert not store.transaction(lambda tx:tx.get('artifacts',meta['id'])).get('received_at')
    _,m=library._manifest('alice',meta['id'])
    assert m['state']=='uploading'
    assert library.saved('alice',meta['id']) is None
    library._read=original
    now[0]+=LEASE_SECONDS+1
    assert library.cleanup_expired()['removed_pilot_artifacts']==1
    assert not store._data['evidence_chunks']
    assert store.transaction(lambda tx:tx.get('evidence_catalog',library.owner_key))['bytes']==0
    assert library._manifest('alice',meta['id'])[1]['state']=='aborted'
    result=save(library,meta)
    assert result['saved']


def test_other_account_and_arbitrary_bundle_denied(cloud_evidence):
    library,store,service,now,meta,raw,calls=cloud_evidence
    result=save(library,meta)
    for action in (lambda:library.save('bob',meta['id'],'x'),lambda:library.saved('bob',meta['id']),
                   lambda:library.list_investigations('bob')):
        with pytest.raises(HostedError):action()
    with pytest.raises(HostedError), library.profile_for('bob',result['bundle_id']):pass
    with pytest.raises(ValueError), library.profile_for('alice','../../secret.json'):pass


def test_quota_reserved_before_download_and_atomic_rejection(cloud_evidence):
    library,store,service,now,meta,raw,calls=cloud_evidence
    store.transaction(lambda tx:tx.set('evidence_catalog',library.owner_key,{'bytes':QUOTA_BYTES,'items':{}}))
    with pytest.raises(ValueError,match='quota'):save(library,meta)
    assert calls==[]
    assert library._manifest('alice',meta['id'])[1] is None


def test_retention_never_deletes_existing_hosted_records(cloud_evidence):
    library,store,service,now,meta,raw,calls=cloud_evidence
    result=save(library,meta)
    existing=store.transaction(lambda tx:tx.get('artifacts',meta['id']))
    now[0]+=RETENTION_SECONDS+1
    assert library.saved('alice',meta['id']) is None
    with pytest.raises(HostedError),library.profile_for('alice',result['bundle_id']):pass
    assert library.cleanup_expired()['removed_pilot_artifacts']==1
    assert library.cleanup_expired()['removed_pilot_artifacts']==0
    assert store.transaction(lambda tx:tx.get('artifacts',meta['id']))==existing
    assert library._manifest('alice',meta['id'])[1]['state']=='expired'


def test_failed_retention_delete_keeps_quota_for_retry(cloud_evidence):
    library,store,service,now,meta,raw,calls=cloud_evidence
    save(library,meta);now[0]+=RETENTION_SECONDS+1
    delete=library.delete_chunks
    library.delete_chunks=lambda *_:(_ for _ in ()).throw(RuntimeError('Unavailable'))
    with pytest.raises(RuntimeError):library.cleanup_expired()
    assert store.transaction(lambda tx:tx.get('evidence_catalog',library.owner_key))['bytes']==len(raw)
    library.delete_chunks=delete
    assert library.cleanup_expired()['removed_pilot_artifacts']==1


def test_corrupt_committed_bytes_cannot_be_rendered(cloud_evidence):
    library,store,service,now,meta,raw,calls=cloud_evidence
    result=save(library,meta)
    key,m=library._manifest('alice',meta['id'])
    chunk=library._chunk_keys(key,m)[0]
    store.transaction(lambda tx:tx.set('evidence_chunks',chunk,{'raw':b'bad'}))
    with pytest.raises(ValueError):library.saved('alice',meta['id'])
    with pytest.raises(ValueError),library.profile_for('alice',result['bundle_id']):pass


def test_pilot_memory_limit_before_download(cloud_evidence):
    library,store,service,now,meta,raw,calls=cloud_evidence
    library.max_artifact=1
    with pytest.raises(ValueError,match='16 MiB'):save(library,meta)
    assert not calls


def test_native_validation_does_not_load_model_runtime(cloud_evidence,tmp_path):
    import os, subprocess
    library,store,service,now,meta,raw,calls=cloud_evidence
    file=tmp_path/'bundle.json';file.write_bytes(raw)
    script='''import sys,json,importlib.abc
class DenyModels(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.split('.')[0] in ('torch','transformers','forking_paths'):
   raise AssertionError('Model runtime imported during evidence validation')
sys.meta_path.insert(0,DenyModels())
from fork_microscope.investigation_bundle import validate_bundle
validate_bundle(json.load(open(sys.argv[1])))
print('validated without model runtime')
'''
    result=subprocess.run([sys.executable,'-c',script,str(file)],capture_output=True,text=True,
                          env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'})
    assert result.returncode==0,result.stderr


def test_retention_winning_receipt_race_cannot_acknowledge_deleted_bytes(cloud_evidence):
    library,store,service,now,meta,raw,calls=cloud_evidence
    receipt=library._receipt
    def raced(*args):
        now[0]+=RETENTION_SECONDS+1
        library.cleanup_expired()
        return receipt(*args)
    library._receipt=raced
    with pytest.raises(ValueError,match='unavailable'):save(library,meta)
    assert not store.transaction(lambda tx:tx.get('artifacts',meta['id'])).get('received_at')


def budgeted(library, store, now):
    from pilot_budget import PilotBudget, COLLECTION
    store.transaction(lambda tx:tx.set(COLLECTION,'pilot_budget_control',dict(enabled=True,
        observed_at=now[0],estimated_total_usd=1.0,month='1970-01')))
    library.admission=PilotBudget(store,pilot_uid='alice',clock=lambda:now[0]).admit


def test_stale_budget_blocks_new_save_before_download_or_receipt(cloud_evidence):
    from pilot_budget import RECONCILIATION_MAX_AGE
    library,store,service,now,meta,raw,calls=cloud_evidence
    budgeted(library,store,now)
    now[0]+=RECONCILIATION_MAX_AGE+1
    with pytest.raises(HostedError) as error:save(library,meta)
    assert error.value.status==503 and calls==[]
    assert library._manifest('alice',meta['id'])[1] is None
    assert store.transaction(lambda tx:tx.get('evidence_catalog',library.owner_key)) is None
    assert not store.transaction(lambda tx:tx.get('artifacts',meta['id'])).get('received_at')


def test_stale_budget_keeps_saved_retrieval_and_retention_cleanup(cloud_evidence):
    from pilot_budget import RECONCILIATION_MAX_AGE
    library,store,service,now,meta,raw,calls=cloud_evidence
    budgeted(library,store,now)
    result=save(library,meta)
    now[0]+=RECONCILIATION_MAX_AGE+1
    assert library.saved('alice',meta['id'])==result
    assert save(library,meta)==result and calls==['download']
    assert library.list_investigations('alice')['total_candidates']==1
    with library.profile_for('alice',result['bundle_id']) as p:
        assert (p['evidence_dir']/result['bundle_id']).read_bytes()==raw
    now[0]+=RETENTION_SECONDS
    assert library.cleanup_expired()['removed_pilot_artifacts']==1
    assert store.transaction(lambda tx:tx.get('evidence_catalog',library.owner_key))['bytes']==0


def test_lost_commit_response_retry_reuses_generation_without_second_download(cloud_evidence):
    library,store,service,now,meta,raw,calls=cloud_evidence
    original,lost=store.transaction,[]
    def lossy(fn):
        value=original(fn)
        if not lost and isinstance(value,dict) and value.get('state')=='committed' and 'identity' in value:
            lost.append(True);raise ConnectionError('Commit response lost')
        return value
    store.transaction=lossy
    with pytest.raises(ConnectionError):save(library,meta)
    assert not store.transaction(lambda tx:tx.get('artifacts',meta['id'])).get('received_at')
    generation=library._manifest('alice',meta['id'])[1]['generation']
    result=save(library,meta)
    assert result['receipt']['status']=='acknowledged' and calls==['download']
    assert library._manifest('alice',meta['id'])[1]['generation']==generation
    assert store.transaction(lambda tx:tx.get('evidence_catalog',library.owner_key))['bytes']==len(raw)


def test_committed_evidence_is_immutable_against_changed_source_metadata(cloud_evidence):
    library,store,service,now,meta,raw,calls=cloud_evidence
    result=save(library,meta)
    store.transaction(lambda tx:tx.set('artifacts',meta['id'],dict(meta,sha256='0'*64)))
    with pytest.raises(ValueError,match='changed'):save(library,meta)
    with library.profile_for('alice',result['bundle_id']) as p:
        assert (p['evidence_dir']/result['bundle_id']).read_bytes()==raw
    assert calls==['download']


def test_all_saved_read_paths_charge_bytes_and_materialization_is_bounded(cloud_evidence):
    library,store,service,now,meta,raw,calls=cloud_evidence
    reads=[]
    library.admission=lambda **kw:reads.append(kw)
    result=save(library,meta)
    library.saved('alice',meta['id'])
    save(library,meta)
    with library.profile_for('alice',result['bundle_id']):
        with pytest.raises(HostedError):library.saved('alice',meta['id'])
    assert [v['byte_count'] for v in reads if v['kind']=='evidence_read']==[len(raw)]*4
