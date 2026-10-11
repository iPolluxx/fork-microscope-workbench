"""Authenticated, durable hosted evidence handoff into private saved libraries.

The caller supplies an authenticated owner and an explicitly configured durable
root. This module is not an MCP tool and never accepts identity from model input.
The injected reader receives transient download capabilities only inside trusted
server code. No capabilities or filesystem paths appear in returned results.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from fork_microscope.hosted.service import JOB_TERMINAL, HostedError
from fork_microscope.investigation_bundle import MAX_IMPORT_BYTES, digest, validate_bundle
from tools.profile import DEFAULT_LIMITS, private, scoped_profile


def _hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _directory(path, *, create=True):
    if path.resolve() != path:
        raise ValueError('Hosted evidence directories cannot traverse symbolic links.')
    if create:
        try:
            path.mkdir(mode=0o700)
            _sync_dir(path.parent)
        except FileExistsError:
            pass
    elif not path.exists():
        return False
    private(path, True)
    return True


def _atomic(path, raw):
    if path.is_symlink():
        raise ValueError('Hosted evidence paths cannot be symbolic links.')
    fd, temporary = tempfile.mkstemp(prefix='.hosted-save-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _json_write(path, value):
    _atomic(path, json.dumps(value, sort_keys=True, allow_nan=False).encode())


def _read(path, limit=MAX_IMPORT_BYTES):
    private(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Hosted evidence exceeds its bounded size limit.')
    return raw


class HostedEvidenceLibrary:
    """Save first, acknowledge second; verified existing saves survive pod removal."""

    def __init__(self, service, *, durable_root, artifact_reader):
        self.service = service
        self.root = Path(durable_root)
        if not self.root.is_absolute() or self.root.parent == self.root:
            raise ValueError('Configure an absolute dedicated durable evidence directory.')
        if not callable(artifact_reader):
            raise ValueError('Configure a trusted hosted artifact reader.')
        self.reader = artifact_reader
        _directory(self.root)

    def _profile_path(self, uid, *, create=True):
        if not isinstance(uid, str) or not 1 <= len(uid) <= 128:
            raise ValueError('An authenticated owner is required.')
        private(self.root, True)
        owner_key = _hash('fork-hosted-owner-v1:' + uid)
        owner_root = self.root / owner_key
        if not _directory(owner_root, create=create):
            return None
        for name in ('evidence', 'exports', 'state'):
            if not _directory(owner_root / name, create=create):
                return None
        path = owner_root / 'profile.json'
        expected = dict(profile_id=owner_key[:32], evidence_dir=str(owner_root / 'evidence'),
                        export_dir=str(owner_root / 'exports'), state_dir=str(owner_root / 'state'),
                        worker_url=None, worker_token_file=None, limits=dict(DEFAULT_LIMITS))
        if path.exists() or path.is_symlink():
            if json.loads(_read(path, 16384)) != expected:
                raise ValueError('Hosted evidence profile configuration changed.')
        else:
            if not create:
                return None
            _json_write(path, expected)
        return path

    @contextmanager
    def profile(self, uid):
        """Bind existing evidence and viewer adapters to the authenticated owner."""
        with scoped_profile(self._profile_path(uid)) as profile:
            yield profile

    @contextmanager
    def _locked_profile(self, uid):
        with self.profile(uid) as profile:
            lock_path = profile['state_dir'] / 'hosted-save.lock'
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                private(lock_path)
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield profile
            finally:
                os.close(fd)

    def _metadata(self, uid, artifact_id):
        artifacts = self.service.public(uid, 'artifacts')['artifacts']
        matches = [item for item in artifacts if item.get('id') == artifact_id]
        if len(matches) != 1 or matches[0].get('owner_uid') != uid:
            raise HostedError(404, 'Artifact not found')
        item = matches[0]
        size = item.get('size', item.get('size_bytes'))
        sha = item.get('sha256')
        if (type(size) is not int or not 1 <= size <= MAX_IMPORT_BYTES
                or not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha)
                or item.get('storage_mode') not in ('device', 'drive')):
            raise ValueError('Artifact lacks verified size, checksum or storage metadata.')
        job_id, session_id = item.get('job_id'), item.get('session_id')
        if not all(isinstance(value, str) and 1 <= len(value) <= 128 for value in (job_id, session_id)):
            raise ValueError('Invalid artifact provenance.')
        job = self.service.public(uid, 'get_job', resource_id=job_id)
        if (job.get('owner_uid') != uid or job.get('id') != job_id
                or job.get('session_id') != session_id or job.get('status') not in JOB_TERMINAL):
            raise ValueError('Artifact source job is unavailable or unfinished.')
        return dict(id=artifact_id, sha256=sha, size=size, job_id=job_id,
                    session_id=session_id, storage_mode=item['storage_mode'])

    def _download(self, uid, metadata):
        try:
            capability = self.service.public(uid, 'download', resource_id=metadata['id'])
            if capability.get('sha256') != metadata['sha256']:
                raise ValueError('Artifact download identity changed.')
            if 'size' in capability and capability['size'] != metadata['size']:
                raise ValueError('Artifact download size changed.')
            content = self.reader(uid, dict(metadata), capability)
            chunks = (content,) if isinstance(content, bytes) else content
            raw = bytearray()
            for chunk in chunks:
                if not isinstance(chunk, bytes) or not chunk:
                    raise ValueError('Invalid artifact download chunk.')
                if len(raw) + len(chunk) > metadata['size']:
                    raise ValueError('Artifact download exceeds declared size.')
                raw.extend(chunk)
            return bytes(raw)
        except Exception:
            # HTTP/reader exceptions can contain bearer capabilities or URLs.
            raise ValueError('Hosted artifact could not be read or failed transfer validation.') from None

    @staticmethod
    def _validate(raw, metadata):
        if len(raw) != metadata['size'] or hashlib.sha256(raw).hexdigest() != metadata['sha256']:
            raise ValueError('Hosted artifact size or checksum verification failed.')
        try:
            value = validate_bundle(json.loads(raw))
        except Exception:
            raise ValueError('Hosted artifact is not a valid native investigation bundle.') from None
        # Engine.start uses hosted job ID as WorkflowManager request_id. Its
        # internal workflow ID is the canonical request digest, not the job ID.
        if value['payload']['investigation'].get('id') != digest(metadata['job_id'])[:32]:
            raise ValueError('Investigation identity differs from its hosted source job.')
        return value

    def save(self, uid, artifact_id, request_id):
        """Durably save owned bytes and return hash-pinned identity, never secrets."""
        if not isinstance(artifact_id, str) or not re.fullmatch(r'[A-Za-z0-9]{1,128}', artifact_id):
            raise ValueError('Invalid hosted artifact identifier.')
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValueError('Use a stable save request ID of 1 to 128 characters.')
        try:
            return self._save(uid, artifact_id, request_id)
        except OSError:
            raise ValueError('Private durable evidence storage is unavailable; receipt was not confirmed.') from None

    def saved(self, uid, artifact_id):
        """Read a verified committed save without downloads, receipts or writes.

        Missing saves return None. Broken committed evidence raises ValueError;
        it must never become a saved-evidence claim in a refreshed workspace.
        Owner metadata is checked through the service's read-only store API.
        """
        if not isinstance(artifact_id, str) or not re.fullmatch(r'[A-Za-z0-9]{1,128}', artifact_id):
            raise ValueError('Invalid hosted artifact identifier.')
        try:
            path = self._profile_path(uid, create=False)
            if path is None:
                return None
            with scoped_profile(path) as profile:
                key = _hash(artifact_id)
                ledger_path = profile['state_dir'] / ('artifact-' + key + '.json')
                if not ledger_path.exists() and not ledger_path.is_symlink():
                    return None
                ledger = json.loads(_read(ledger_path, 16384))
                metadata = self._metadata(uid, artifact_id)
                if ledger.get('metadata') != metadata:
                    raise ValueError('Previously saved artifact metadata changed.')
                bundle_id = 'hosted-' + key + '-' + metadata['sha256'] + '.json'
                if ledger.get('bundle_id') != bundle_id:
                    raise ValueError('Previously saved artifact identity changed.')
                value = self._validate(_read(profile['evidence_dir'] / bundle_id), metadata)
                receipt = ledger.get('receipt')
                if metadata['storage_mode'] == 'drive':
                    receipt = dict(status='not_required', cleanup_requested=None)
                elif receipt is None:
                    receipt = dict(status='pending_retry', cleanup_requested=None)
                return self._result(metadata, value, bundle_id, receipt)
        except OSError:
            raise ValueError('Previously saved evidence is missing or unreadable.') from None

    @staticmethod
    def _result(metadata, value, bundle_id, receipt):
        if (not isinstance(receipt, dict) or set(receipt) != {'status', 'cleanup_requested'}
                or receipt['status'] not in {'acknowledged', 'pending_retry', 'not_required'}
                or (receipt['cleanup_requested'] is not None and type(receipt['cleanup_requested']) is not bool)):
            raise ValueError('Invalid saved evidence receipt metadata.')
        return dict(saved=True, server_saved=True, user_download_confirmed=False,
                    artifact_id=metadata['id'], bundle_id=bundle_id,
                    file_sha256=metadata['sha256'], bytes=metadata['size'],
                    payload_sha256=value['sha256'],
                    evidence_uri='fork-evidence://sha256/' + metadata['sha256'],
                    receipt=receipt, cleanup_verified=False,
                    message='Validated original evidence is durably saved in your private server library. '
                            'This does not confirm a download to your device or completed compute cleanup.')

    def _save(self, uid, artifact_id, request_id):
        with self._locked_profile(uid) as profile:
            metadata = self._metadata(uid, artifact_id)
            state = profile['state_dir']
            key = _hash(artifact_id)
            request_path = state / ('request-' + _hash(request_id) + '.json')
            request = dict(artifact_id=artifact_id, sha256=metadata['sha256'])
            if request_path.exists() or request_path.is_symlink():
                if json.loads(_read(request_path, 4096)) != request:
                    raise ValueError('Save request ID already belongs to a different artifact.')
            else:
                _json_write(request_path, request)
            ledger_path = state / ('artifact-' + key + '.json')
            ledger = None
            if ledger_path.exists() or ledger_path.is_symlink():
                ledger = json.loads(_read(ledger_path, 16384))
                if ledger.get('metadata') != metadata:
                    raise ValueError('Previously saved artifact metadata changed.')
            bundle_id = 'hosted-' + key + '-' + metadata['sha256'] + '.json'
            destination = profile['evidence_dir'] / bundle_id
            if destination.exists() or destination.is_symlink():
                raw = _read(destination)
            else:
                if ledger:
                    raise ValueError('Previously saved evidence is missing; receipt will not be repeated.')
                raw = self._download(uid, metadata)
            value = self._validate(raw, metadata)
            if not destination.exists():
                _atomic(destination, raw)
            # Also sync a recovered existing file before acknowledging after a
            # process crash between its rename and the ledger commit.
            fd = os.open(destination, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            _sync_dir(destination.parent)
            if ledger is None:
                ledger = dict(metadata=metadata, bundle_id=bundle_id, receipt=None)
                _json_write(ledger_path, ledger)
            receipt = ledger['receipt']
            if metadata['storage_mode'] == 'device' and receipt is None:
                try:
                    acknowledged = self.service.public(uid, 'received', resource_id=artifact_id,
                                                       idempotency_key='evidence-receipt:' + key)
                    if acknowledged.get('received') is not True:
                        raise ValueError('Receipt not confirmed.')
                    receipt = dict(status='acknowledged', cleanup_requested=acknowledged.get('desired_state') == 'terminated')
                except Exception:
                    receipt = dict(status='pending_retry', cleanup_requested=None)
                else:
                    ledger['receipt'] = receipt
                    _json_write(ledger_path, ledger)
            if metadata['storage_mode'] == 'drive':
                receipt = dict(status='not_required', cleanup_requested=None)
            return self._result(metadata, value, bundle_id, receipt)
