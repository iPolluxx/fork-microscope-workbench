"""Fake-only hosted save/receipt tests; no provider, account or model calls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tests'))
from test_evidence_io import archive
from offline_support import adapter_context
from fork_microscope.hosted.service import HostedError, HostedService
from fork_microscope.hosted.store import MemoryStore
from fork_microscope.investigation_bundle import build_bundle, digest
from tools.hosted_evidence import HostedEvidenceLibrary
from tools.profile import load_profile


@pytest.fixture
def saved(tmp_path, archive):
    uid, artifact_id, job_id, session_id = 'alice', 'a' * 32, 'b' * 32, 'c' * 32
    raw = json.dumps(build_bundle([archive], investigation={'id': digest(job_id)[:32]}), indent=2).encode()
    store = MemoryStore()
    service = HostedService(store, clock=lambda: 100)
    metadata = dict(id=artifact_id, owner_uid=uid, job_id=job_id, session_id=session_id,
                    storage_mode='device', size=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                    download_token='fake-secret-capability-never-expose')
    def seed(tx):
        tx.set('artifacts', artifact_id, metadata)
        tx.set('jobs', job_id, dict(id=job_id, owner_uid=uid, session_id=session_id, status='completed'))
        tx.set('sessions', session_id, dict(id=session_id, owner_uid=uid, expires_at=500,
                                          observed_state='ready', desired_state='running', provider_ref='testpod'))
    store.transaction(seed)
    calls = []
    def reader(owner, item, capability):
        assert owner == uid and item['sha256'] == metadata['sha256']
        assert capability['download_token'] == metadata['download_token']
        calls.append(item['id'])
        return (raw[:100], raw[100:])
    library = HostedEvidenceLibrary(service, durable_root=tmp_path / 'durable', artifact_reader=reader)
    return library, service, metadata, raw, calls


def mutate(service, collection, key, **changes):
    def apply(tx):
        item = tx.get(collection, key)
        item.update(changes)
        tx.set(collection, key, item)
    service.store.transaction(apply)


def test_verified_durable_save_then_receipt_and_same_bytes_viewer(saved):
    library, service, metadata, raw, calls = saved
    original = service.public
    receipt_saw_durable = []
    def public(uid, operation, *args, **kwargs):
        if operation == 'received':
            with library.profile(uid) as profile:
                bundles = list(profile['evidence_dir'].glob('*.json'))
                ledgers = list(profile['state_dir'].glob('artifact-*.json'))
                receipt_saw_durable.append(bundles[0].read_bytes() == raw and len(ledgers) == 1)
        return original(uid, operation, *args, **kwargs)
    service.public = public
    result = library.save('alice', metadata['id'], 'save-1')
    assert result['saved'] and result['server_saved'] and not result['user_download_confirmed']
    assert result['receipt'] == dict(status='acknowledged', cleanup_requested=True)
    assert not result['cleanup_verified'] and receipt_saw_durable == [True]
    assert calls == [metadata['id']]
    text = json.dumps(result)
    assert 'fake-secret' not in text and 'proxy.runpod.net' not in text and str(library.root) not in text
    with adapter_context(), library.profile('alice') as profile:
        evidence = importlib.import_module('tools.evidence')
        identity = evidence.fork_microscope_list_investigations()['investigations'][0]
        assert identity['bundle_id'] == result['bundle_id']
        assert identity['file_sha256'] == result['file_sha256'] == metadata['sha256']
        assert (profile['evidence_dir'] / result['bundle_id']).read_bytes() == raw
        assert (profile['evidence_dir'] / result['bundle_id']).stat().st_mode & 0o777 == 0o600
        for name in ('evidence_dir', 'export_dir', 'state_dir'):
            assert profile[name].stat().st_mode & 0o777 == 0o700


def test_duplicate_save_and_restart_survive_deleted_pod(saved):
    library, service, metadata, raw, calls = saved
    first = library.save('alice', metadata['id'], 'save-1')
    mutate(service, 'sessions', metadata['session_id'], observed_state='terminated', cleanup_verified=True, expires_at=0)
    def forbidden(*_):
        pytest.fail('Existing verified evidence must not require the deleted pod')
    restarted = HostedEvidenceLibrary(service, durable_root=library.root, artifact_reader=forbidden)
    assert restarted.save('alice', metadata['id'], 'save-1') == first
    assert restarted.save('alice', metadata['id'], 'save-again') == first
    assert calls == [metadata['id']]


def test_concurrent_duplicate_saves_fetch_and_receipt_once(saved):
    library, service, metadata, _, calls = saved
    original = service.public
    receipts = []
    def public(uid, operation, *args, **kwargs):
        if operation == 'received':
            receipts.append(kwargs['resource_id'])
        return original(uid, operation, *args, **kwargs)
    service.public = public
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: library.save('alice', metadata['id'], 'same-save'), range(4)))
    assert all(result == results[0] for result in results)
    assert calls == receipts == [metadata['id']]


def test_real_coordinator_export_identity_with_fake_execution(saved, tmp_path, archive):
    from test_workflow import FakeService, config, finished
    from fork_microscope.investigation_bundle import canonical
    from fork_microscope.investigation_workflow import WorkflowManager
    library, service, metadata, _, _ = saved
    manager = WorkflowManager(FakeService(archive), tmp_path / 'fake-coordinator')
    workflow = manager.start(dict(request_id=metadata['job_id'], config=config.__wrapped__()))
    assert finished(manager, workflow['id'])['status'] == 'complete'
    assert workflow['id'] != metadata['job_id']
    raw = canonical(manager.export(workflow['id']))
    mutate(service, 'artifacts', metadata['id'], size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    library.reader = lambda *_: raw
    result = library.save('alice', metadata['id'], 'real-coordinator-fake-engine')
    assert result['saved'] and result['file_sha256'] == hashlib.sha256(raw).hexdigest()


def test_failed_evidence_fsync_never_acknowledges(saved, monkeypatch):
    library, service, metadata, _, _ = saved
    import tools.hosted_evidence as module
    original = module._sync_dir
    def fail_evidence(path):
        if path.name == 'evidence' and list(path.glob('*.json')):
            raise OSError('durability could not be established')
        return original(path)
    monkeypatch.setattr(module, '_sync_dir', fail_evidence)
    with pytest.raises(ValueError, match='storage is unavailable'):
        library.save('alice', metadata['id'], 'save-1')
    assert 'received_at' not in service.public('alice', 'artifacts')['artifacts'][0]


def test_receipt_timeout_keeps_save_and_retries_after_pod_disappears(saved):
    library, service, metadata, raw, calls = saved
    original = service.public
    failures = [True]
    def public(uid, operation, *args, **kwargs):
        if operation == 'received' and failures:
            failures.pop()
            # Simulate a response lost after the server committed the receipt.
            original(uid, operation, *args, **kwargs)
            raise TimeoutError('https://private.invalid/capability=secret')
        return original(uid, operation, *args, **kwargs)
    service.public = public
    first = library.save('alice', metadata['id'], 'save-1')
    assert first['saved'] and first['receipt']['status'] == 'pending_retry'
    assert 'secret' not in json.dumps(first)
    mutate(service, 'sessions', metadata['session_id'], observed_state='terminated', cleanup_verified=True)
    second = library.save('alice', metadata['id'], 'save-1')
    assert second['receipt']['status'] == 'acknowledged'
    assert second['bundle_id'] == first['bundle_id'] and len(calls) == 1


def test_crash_after_bundle_before_ledger_recovers_without_download(saved, monkeypatch):
    library, service, metadata, raw, calls = saved
    import tools.hosted_evidence as module
    original = module._json_write
    def fail_ledger(path, value):
        if path.name.startswith('artifact-'):
            raise OSError('disk unavailable')
        return original(path, value)
    monkeypatch.setattr(module, '_json_write', fail_ledger)
    with pytest.raises(ValueError, match='storage is unavailable'):
        library.save('alice', metadata['id'], 'save-1')
    assert 'received_at' not in service.public('alice', 'artifacts')['artifacts'][0]
    monkeypatch.setattr(module, '_json_write', original)
    mutate(service, 'sessions', metadata['session_id'], observed_state='terminated')
    assert library.save('alice', metadata['id'], 'save-1')['receipt']['status'] == 'acknowledged'
    assert len(calls) == 1


def test_cross_owner_access_and_scoped_viewer_do_not_leak(saved):
    library, service, metadata, raw, calls = saved
    result = library.save('alice', metadata['id'], 'save-1')
    with pytest.raises(HostedError) as error:
        library.save('bob', metadata['id'], 'save-1')
    assert error.value.status == 404
    with adapter_context():
        evidence = importlib.import_module('tools.evidence')
        with library.profile('bob'):
            assert evidence.fork_microscope_list_investigations()['investigations'] == []
            with pytest.raises(FileNotFoundError):
                evidence._load(result['bundle_id'])
        with library.profile('alice'):
            assert len(evidence.fork_microscope_list_investigations()['investigations']) == 1


def test_context_local_profile_isolation_and_restoration(saved, monkeypatch):
    library, *_ = saved
    with library.profile('operator') as operator:
        operator_path = operator['state_dir'].parent / 'profile.json'
    monkeypatch.setenv('FORK_MICROSCOPE_PROFILE', str(operator_path))
    before = load_profile()['profile_id']
    async def owner(uid):
        with library.profile(uid) as selected:
            await asyncio.sleep(0)
            assert load_profile()['profile_id'] == selected['profile_id']
            return selected['profile_id']
    async def run():
        return await asyncio.gather(owner('alice'), owner('bob'))
    assert len(set(asyncio.run(run()))) == 2
    assert load_profile()['profile_id'] == before
    with pytest.raises(RuntimeError), library.profile('alice'):
        raise RuntimeError('request failed')
    assert load_profile()['profile_id'] == before


@pytest.mark.parametrize('artifact_id', ['../outside', '/tmp/a', 'a\\b', '', 'x' * 129])
def test_path_traversal_identifiers_are_rejected(saved, artifact_id):
    with pytest.raises(ValueError, match='identifier'):
        saved[0].save('alice', artifact_id, 'save-1')


def test_request_id_cannot_switch_artifacts(saved):
    library, service, metadata, raw, calls = saved
    library.save('alice', metadata['id'], 'save-1')
    other = dict(metadata, id='d' * 32)
    service.store.transaction(lambda tx: tx.set('artifacts', other['id'], other))
    with pytest.raises(ValueError, match='different artifact'):
        library.save('alice', other['id'], 'save-1')
    assert len(calls) == 1


def test_metadata_change_or_saved_file_corruption_never_acknowledges(saved):
    library, service, metadata, raw, calls = saved
    result = library.save('alice', metadata['id'], 'save-1')
    with library.profile('alice') as profile:
        (profile['evidence_dir'] / result['bundle_id']).write_bytes(raw + b' ')
    with pytest.raises(ValueError, match='checksum'):
        library.save('alice', metadata['id'], 'save-1')
    mutate(service, 'artifacts', metadata['id'], size=len(raw) + 1)
    with pytest.raises(ValueError, match='metadata changed'):
        library.save('alice', metadata['id'], 'new-request')


@pytest.mark.parametrize('case', ['sha', 'short', 'long', 'chunk_type', 'timeout', 'native', 'wrong_job'])
def test_unverified_download_never_gets_receipt(saved, case):
    library, service, metadata, raw, calls = saved
    data = raw
    if case == 'sha':
        mutate(service, 'artifacts', metadata['id'], sha256='0' * 64)
    elif case == 'short':
        data = raw[:-1]
    elif case == 'long':
        data = raw + b'x'
    elif case == 'chunk_type':
        data = ['not bytes']
    elif case == 'native':
        data = b'{"not":"native evidence"}'
    elif case == 'wrong_job':
        bundle = json.loads(raw)
        bundle['payload']['investigation']['id'] = 'e' * 32
        bundle['sha256'] = digest(bundle['payload'])
        data = json.dumps(bundle).encode()
    if case in ('native', 'wrong_job'):
        mutate(service, 'artifacts', metadata['id'], size=len(data), sha256=hashlib.sha256(data).hexdigest())
    def reader(*_):
        if case == 'timeout':
            raise TimeoutError('download_token=must-never-leak')
        return data
    library.reader = reader
    with pytest.raises(ValueError) as error:
        library.save('alice', metadata['id'], 'save-1')
    assert 'must-never-leak' not in str(error.value)
    assert 'received_at' not in service.public('alice', 'artifacts')['artifacts'][0]
    with library.profile('alice') as profile:
        assert not list(profile['evidence_dir'].glob('*.json'))


def test_oversized_metadata_rejected_before_fetch(saved):
    library, service, metadata, _, calls = saved
    mutate(service, 'artifacts', metadata['id'], size=64 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match='size'):
        library.save('alice', metadata['id'], 'save-1')
    assert calls == []


def test_device_expiry_fails_save_without_receipt(saved):
    library, service, metadata, _, calls = saved
    mutate(service, 'sessions', metadata['session_id'], expires_at=0)
    with pytest.raises(ValueError, match='could not be read'):
        library.save('alice', metadata['id'], 'save-1')
    assert calls == []
    assert 'received_at' not in service.public('alice', 'artifacts')['artifacts'][0]


def test_drive_save_requires_verified_size_and_never_device_receipt(saved):
    library, service, metadata, raw, _ = saved
    mutate(service, 'artifacts', metadata['id'], storage_mode='drive', file_ref='private-drive-ref')
    library.reader = lambda _uid, _metadata, _capability: raw
    result = library.save('alice', metadata['id'], 'save-1')
    assert result['receipt']['status'] == 'not_required'
    assert 'received_at' not in service.public('alice', 'artifacts')['artifacts'][0]
    assert 'private-drive-ref' not in json.dumps(result)


def test_symlinked_root_and_owner_directory_are_rejected(saved, tmp_path):
    library, service, metadata, _, _ = saved
    link = tmp_path / 'alias'
    link.symlink_to(library.root, target_is_directory=True)
    with pytest.raises(ValueError, match='symbolic'):
        HostedEvidenceLibrary(service, durable_root=link, artifact_reader=library.reader)
    with library.profile('alice') as profile:
        evidence_dir = profile['evidence_dir']
    evidence_dir.rmdir()
    outside = tmp_path / 'outside'
    outside.mkdir(mode=0o700)
    evidence_dir.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='symbolic'):
        library.save('alice', metadata['id'], 'save-1')
    assert not list(outside.iterdir())


def test_symlinked_saved_bundle_is_never_followed(saved, tmp_path):
    library, _, metadata, raw, _ = saved
    result = library.save('alice', metadata['id'], 'save-1')
    outside = tmp_path / 'outside.json'
    outside.write_bytes(raw)
    with library.profile('alice') as profile:
        path = profile['evidence_dir'] / result['bundle_id']
        path.unlink()
        path.symlink_to(outside)
    with pytest.raises(ValueError, match='symlink'):
        library.save('alice', metadata['id'], 'save-1')


def test_saved_absent_is_read_only_and_does_not_create_owner_profile(saved, monkeypatch):
    library, service, metadata, _, calls = saved
    import tools.hosted_evidence as module
    def forbidden(*_args, **_kwargs):
        pytest.fail('Absent saved lookup must not write or call an artifact service')
    monkeypatch.setattr(module, '_json_write', forbidden)
    monkeypatch.setattr(module, '_sync_dir', forbidden)
    monkeypatch.setattr(service, 'public', forbidden)
    before = list(library.root.rglob('*'))
    assert library.saved('alice', metadata['id']) is None
    assert library.saved('bob', metadata['id']) is None
    assert list(library.root.rglob('*')) == before
    assert calls == []


def test_saved_refresh_recovers_identity_without_write_download_or_receipt(saved, monkeypatch):
    library, service, metadata, _, calls = saved
    first = library.save('alice', metadata['id'], 'save-1')
    mutate(service, 'sessions', metadata['session_id'], observed_state='terminated', cleanup_verified=True, expires_at=0)
    import tools.hosted_evidence as module
    original = service.public
    operations = []
    def read_only(uid, operation, *args, **kwargs):
        assert operation in ('artifacts', 'get_job')
        operations.append(operation)
        return original(uid, operation, *args, **kwargs)
    def forbidden(*_args, **_kwargs):
        pytest.fail('Saved lookup must not write, sync or fetch bytes')
    monkeypatch.setattr(service, 'public', read_only)
    monkeypatch.setattr(library, 'reader', forbidden)
    monkeypatch.setattr(module, '_json_write', forbidden)
    monkeypatch.setattr(module, '_sync_dir', forbidden)
    assert library.saved('alice', metadata['id']) == first
    assert operations == ['artifacts', 'get_job']
    assert calls == [metadata['id']]
    assert library.saved('bob', metadata['id']) is None


@pytest.mark.parametrize('change', ['missing', 'corrupt', 'metadata', 'ledger_identity'])
def test_saved_refresh_rejects_broken_persisted_evidence(saved, change):
    library, service, metadata, raw, _ = saved
    result = library.save('alice', metadata['id'], 'save-1')
    with library.profile('alice') as profile:
        path = profile['evidence_dir'] / result['bundle_id']
        if change == 'missing':
            path.unlink()
        elif change == 'corrupt':
            path.write_bytes(raw + b'x')
        elif change == 'ledger_identity':
            ledger = next(profile['state_dir'].glob('artifact-*.json'))
            value = json.loads(ledger.read_bytes())
            value['bundle_id'] = '../outside.json'
            ledger.write_text(json.dumps(value))
    if change == 'metadata':
        mutate(service, 'artifacts', metadata['id'], sha256='0' * 64)
    with pytest.raises(ValueError):
        library.saved('alice', metadata['id'])


def test_saved_pending_receipt_is_reported_without_retry(saved):
    library, service, metadata, _, _ = saved
    original = service.public
    receipt_attempts = []
    def public(uid, operation, *args, **kwargs):
        if operation == 'received':
            receipt_attempts.append(operation)
            raise TimeoutError('fixture receipt unavailable')
        return original(uid, operation, *args, **kwargs)
    service.public = public
    first = library.save('alice', metadata['id'], 'save-1')
    assert first['receipt']['status'] == 'pending_retry'
    assert library.saved('alice', metadata['id']) == first
    assert receipt_attempts == ['received']


def test_saved_metadata_is_owner_checked_even_if_local_files_are_copied(saved):
    import shutil
    library, _, metadata, _, _ = saved
    library.save('alice', metadata['id'], 'save-1')
    with library.profile('alice') as alice, library.profile('bob') as bob:
        for directory in ('evidence_dir', 'state_dir'):
            for path in alice[directory].glob('*.json'):
                shutil.copyfile(path, bob[directory] / path.name)
                (bob[directory] / path.name).chmod(0o600)
    with pytest.raises(HostedError) as error:
        library.saved('bob', metadata['id'])
    assert error.value.status == 404
