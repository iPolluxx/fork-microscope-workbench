"""One-account pilot evidence: verified Firestore bytes, then worker receipt.

Only newly written pilot_v1 manifests participate in retention. Existing hosted
artifacts, Drive files, provider credentials and compute are never deleted here.
The small catalog is an explicit bounded index; chunk collections are never scanned.
"""
from contextlib import contextmanager
import hashlib
import json
import re
import secrets
import tempfile
import threading

from fork_microscope.hosted.service import HostedError
from fork_microscope.investigation_bundle import validate_bundle
from tools.hosted_evidence import HostedEvidenceLibrary, _atomic, _hash

CHUNK_BYTES = 512 * 1024
MAX_ARTIFACT = 16 * 1024 * 1024  # Conservative pilot memory limit; native format allows 64 MiB.
QUOTA_BYTES = 1024 ** 3
MAX_ARTIFACTS = 64
RETENTION_SECONDS = 30 * 86400
LEASE_SECONDS = 900
MAX_JSON_STRUCTURE = 250000
MAX_JSON_DEPTH = 64


def check_json_complexity(raw):
    """Bound object expansion before json.loads allocates a Python object graph.

    This is a resource preflight, not a parser. The native validator still checks
    JSON syntax, checksums and schema. Structural characters inside strings are
    ignored, including escaped quotes and backslashes.
    """
    quoted = escaped = False
    depth = structures = 0
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (91,123,93,125,44):
            structures += 1
            if char in (91,123):
                depth += 1
            elif char in (93,125):
                depth -= 1
            if structures > MAX_JSON_STRUCTURE or depth > MAX_JSON_DEPTH:
                raise ValueError('Evidence is too complex for the private pilot')


class PilotEvidenceLibrary(HostedEvidenceLibrary):
    def __init__(self, service, *, store, pilot_uid, artifact_reader, delete_chunks,
                 clock, admission=lambda **_: None, max_artifact=MAX_ARTIFACT):
        if not isinstance(pilot_uid, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', pilot_uid):
            raise ValueError('Exact pilot account is required')
        if type(max_artifact) is not int or not 1 <= max_artifact <= MAX_ARTIFACT:
            raise ValueError('Invalid pilot artifact bound')
        self.service, self.store, self.uid = service, store, pilot_uid
        self.reader, self.delete_chunks, self.clock = artifact_reader, delete_chunks, clock
        self.admission, self.max_artifact = admission, max_artifact
        self.owner_key = _hash('fork-hosted-owner-v1:' + pilot_uid)
        if service.store is not store:
            raise ValueError('Pilot evidence and hosted receipts must share one transactional store')
        self._materialization = threading.BoundedSemaphore(1)

    def _owner(self, uid):
        if uid != self.uid:
            raise HostedError(404, 'Evidence not found')

    def _validate(self, raw, metadata):
        if len(raw) > self.max_artifact:
            raise ValueError('Evidence exceeds the private pilot size limit')
        check_json_complexity(raw)
        if metadata.get('storage_mode') == 'imported':
            if len(raw) != metadata['size'] or hashlib.sha256(raw).hexdigest() != metadata['sha256']:
                raise ValueError('Imported evidence size or checksum verification failed')
            def pairs(items):
                value = {}
                for key, item in items:
                    if key in value:
                        raise ValueError('Duplicate JSON fields are not accepted')
                    value[key] = item
                return value
            def constant(_):
                raise ValueError('Nonfinite JSON values are not accepted')
            try:
                return validate_bundle(json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                                                  parse_constant=constant))
            except Exception:
                raise ValueError('Import a valid UTF-8 native investigation JSON bundle') from None
        return super()._validate(raw, metadata)

    def import_bytes(self, uid, raw, expected_sha256, request_id, *, synthetic=False):
        """Import data only. Never fetch a URL, create compute records or acknowledge a worker."""
        self._owner(uid)
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= self.max_artifact:
            raise HostedError(413, 'Choose a native investigation JSON file of at most 16 MiB')
        if not isinstance(expected_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_sha256):
            raise HostedError(422, 'The file SHA-256 is required')
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise HostedError(422, 'File checksum mismatch; nothing was imported')
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request_id):
            raise HostedError(422, 'Use a stable import request ID of 1 to 128 characters')
        if type(synthetic) is not bool:
            raise HostedError(422, 'Synthetic evidence label must be true or false')
        artifact_id = _hash('fork-import-v1:' + expected_sha256)
        metadata = dict(id=artifact_id, sha256=expected_sha256, size=len(raw), storage_mode='imported',
                        provenance=dict(source='user_file', synthetic=synthetic, execution_verified=False))
        with self._memory_slot():
            # Invalid input must not reserve quota or alter idempotency records.
            try:
                self._validate(raw, metadata)
            except ValueError as error:
                raise HostedError(422, str(error)) from None
            return self._import_validated(uid, raw, metadata, request_id)

    def _import_validated(self, uid, raw, metadata, request_id):
        key = self._key(uid, metadata['id'])
        request_key = self.owner_key + '_' + _hash('import:' + request_id)
        now = self.clock()
        def reserve(tx):
            request = tx.get('evidence_requests', request_key)
            if request and (request.get('artifact_key') != key or request.get('metadata') != metadata):
                raise HostedError(409, 'Import request ID belongs to a different file or evidence label')
            live = tx.get('evidence_manifests', key)
            if live:
                if live.get('owner_uid') != uid or live.get('policy') != 'pilot_v1' or live['metadata'] != metadata:
                    raise HostedError(409, 'This file was imported with different provenance')
                if live['state'] == 'committed' and live['expires_at'] > now:
                    self.admission(tx=tx, kind='request')
                    tx.set('evidence_requests', request_key, dict(artifact_key=key, metadata=metadata, created_at=now))
                    return live, True
                if live['state'] == 'uploading' and live['lease_until'] > now:
                    if not request:
                        # Reloading the first-party form loses its in-memory ID.
                        # Exact validated content/provenance can join this owned
                        # generation, without a second reservation or new lease.
                        self.admission(tx=tx, kind='request')
                        tx.set('evidence_requests', request_key, dict(artifact_key=key, metadata=metadata, created_at=now))
                    return live, False
                if live['state'] == 'expired' and request:
                    raise HostedError(410, 'This import expired; a new explicit import request is required')
                if live['state'] not in ('aborted', 'expired'):
                    raise HostedError(409, 'Incomplete or expired import awaits retention cleanup; retry later')
            catalog = tx.get('evidence_catalog', self.owner_key) or {'bytes':0, 'items':{}}
            if catalog['bytes'] + metadata['size'] > QUOTA_BYTES or len(catalog['items']) >= MAX_ARTIFACTS:
                raise HostedError(409, 'Private evidence quota reached; export retained evidence before adding more')
            self.admission(tx=tx, kind='evidence_save', byte_count=metadata['size'])
            manifest = dict(policy='pilot_v1', owner_uid=uid, metadata=metadata, state='uploading',
                generation=secrets.token_hex(16), chunks=(metadata['size']+CHUNK_BYTES-1)//CHUNK_BYTES,
                created_at=now, expires_at=now+RETENTION_SECONDS, lease_until=now+LEASE_SECONDS,
                receipt=dict(status='not_required', cleanup_requested=None),
                bundle_id='hosted-'+_hash(metadata['id'])+'-'+metadata['sha256']+'.json')
            catalog['bytes'] += metadata['size']
            catalog['items'][key] = metadata['id']
            tx.set('evidence_catalog', self.owner_key, catalog)
            tx.set('evidence_manifests', key, manifest)
            tx.set('evidence_requests', request_key, dict(artifact_key=key, metadata=metadata, created_at=now))
            return manifest, False
        manifest, deduplicated = self.store.transaction(reserve)
        if not deduplicated:
            try:
                keys = self._chunk_keys(key, manifest)
                for start in range(0, len(keys), 8):
                    def write(tx):
                        live = tx.get('evidence_manifests', key)
                        if (not live or live['generation'] != manifest['generation'] or
                            live['state'] != 'uploading' or live['lease_until'] <= self.clock()):
                            raise ValueError('Import lease changed or expired')
                        for i in range(start, min(start+8, len(keys))):
                            tx.set('evidence_chunks', keys[i], {'raw':raw[i*CHUNK_BYTES:(i+1)*CHUNK_BYTES]})
                    self.store.transaction(write)
                readback, value = self._read(key, manifest)
                from tools.evidence import _identity
                identity = _identity(manifest['bundle_id'], value, readback)
                identity['provenance'] = metadata['provenance']
                def commit(tx):
                    live = tx.get('evidence_manifests', key)
                    if (not live or live['generation'] != manifest['generation'] or
                        live['state'] != 'uploading' or live['lease_until'] <= self.clock() or
                        live['expires_at'] <= self.clock()):
                        raise ValueError('Import expired before commit')
                    live.update(state='committed', identity=identity)
                    tx.set('evidence_manifests', key, live)
                    return live
                manifest = self.store.transaction(commit)
            except Exception:
                # A lost commit response must not create another generation or claim partial data saved.
                live = self.store.transaction(lambda tx:tx.get('evidence_manifests', key))
                if (not live or live['state'] != 'committed' or live['generation'] != manifest['generation']
                        or live['expires_at'] <= self.clock()):
                    raise HostedError(503, 'Import incomplete; retry the same file and request ID') from None
                manifest = live
        _, value = self._read(key, manifest)
        result = self._result(metadata, value, manifest['bundle_id'], manifest['receipt'])
        result.update(provenance=metadata['provenance'], cleanup_applicable=False, deduplicated=deduplicated,
                      message='Imported original bytes are verified and saved privately. '
                              'Imported provenance does not verify platform execution or compute cleanup.')
        return result

    def _key(self, uid, artifact_id):
        self._owner(uid)
        if not isinstance(artifact_id, str) or not re.fullmatch(r'[A-Za-z0-9]{1,128}', artifact_id):
            raise ValueError('Invalid artifact identifier')
        return self.owner_key + '_' + _hash(artifact_id)

    @staticmethod
    def _chunk_keys(key, manifest):
        return [key + '_' + manifest['generation'] + '_' + str(i)
                for i in range(manifest['chunks'])]

    def _manifest(self, uid, artifact_id):
        key = self._key(uid, artifact_id)
        item = self.store.transaction(lambda tx: tx.get('evidence_manifests', key))
        if item is None:
            return key, None
        if item.get('policy') != 'pilot_v1' or item.get('owner_uid') != uid:
            raise ValueError('Evidence ownership verification failed')
        return key, item

    def _read(self, key, manifest):
        if manifest['state'] not in ('uploading', 'committed'):
            raise ValueError('Evidence is unavailable or expired')
        size = manifest['metadata']['size']
        if (type(size) is not int or not 1 <= size <= self.max_artifact
                or type(manifest['chunks']) is not int or manifest['chunks'] != (size+CHUNK_BYTES-1)//CHUNK_BYTES):
            raise ValueError('Invalid evidence manifest bounds')
        self.admission(kind='evidence_read',byte_count=size)
        parts = []
        for chunk_key in self._chunk_keys(key, manifest):
            chunk = self.store.transaction(lambda tx: tx.get('evidence_chunks', chunk_key))
            if not chunk or type(chunk.get('raw')) is not bytes or not 1 <= len(chunk['raw']) <= CHUNK_BYTES:
                raise ValueError('Saved evidence is incomplete')
            parts.append(chunk['raw'])
        raw = b''.join(parts)
        return raw, self._validate(raw, manifest['metadata'])

    def _receipt(self, uid, key, manifest, value):
        # The manifest check, core 'received' metadata transition and saved
        # receipt commit share ONE transaction. Retention claims the same
        # manifest, so it cannot delete between that check and acknowledgment.
        def valid(live):
            return (live and live.get('owner_uid') == uid and live['state'] == 'committed'
                    and live['generation'] == manifest['generation']
                    and live['metadata'] == manifest['metadata'] and live['expires_at'] > self.clock())
        def commit(tx):
            live = tx.get('evidence_manifests',key)
            if not valid(live):
                raise ValueError('Saved evidence expired before its receipt could be confirmed')
            receipt = live.get('receipt')
            if receipt is None:
                answer = self.service._operation(tx,uid,'received',{},manifest['metadata']['id'],self.clock(),None)
                if answer.get('received') is not True:
                    raise ValueError('Receipt not confirmed')
                receipt = dict(status='acknowledged',cleanup_requested=answer.get('desired_state') == 'terminated')
                live['receipt'] = receipt
                tx.set('evidence_manifests',key,live)
            return receipt
        try:
            receipt = self.store.transaction(commit)
        except Exception:
            # Resolve uncertain commit without claiming missing/expired evidence.
            live = self.store.transaction(lambda tx:tx.get('evidence_manifests',key))
            if not valid(live):
                raise ValueError('Saved evidence is unavailable; receipt was not confirmed') from None
            receipt = live.get('receipt') or dict(status='pending_retry',cleanup_requested=None)
        return self._result(manifest['metadata'], value, manifest['bundle_id'], receipt)

    def save(self, uid, artifact_id, request_id):
        with self._memory_slot():
            return self._save_cloud(uid,artifact_id,request_id)

    def _save_cloud(self, uid, artifact_id, request_id):
        key, prior = self._manifest(uid, artifact_id)
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request_id):
            raise ValueError('Stable save request ID is required')
        metadata = self._metadata(uid, artifact_id)
        if metadata['storage_mode'] != 'device' or metadata['size'] > self.max_artifact:
            raise ValueError('Pilot requires device evidence of at most 16 MiB')
        if prior and prior['metadata'] != metadata:
            raise ValueError('Artifact metadata changed')
        now, generation = self.clock(), secrets.token_hex(16)
        request_key = self.owner_key + '_' + _hash(request_id)
        def reserve(tx):
            request = tx.get('evidence_requests', request_key)
            if request and request['artifact_key'] != key:
                raise ValueError('Save request belongs to another artifact')
            live = tx.get('evidence_manifests', key)
            if live:
                if live['state'] == 'committed' and live['expires_at'] > now and live['metadata'] == metadata:
                    return live
                if live['state'] != 'aborted' or live['metadata'] != metadata:
                    raise ValueError('Save already pending or expired; retry after retention cleanup')
            catalog = tx.get('evidence_catalog', self.owner_key) or {'bytes': 0, 'items': {}}
            if catalog['bytes'] + metadata['size'] > QUOTA_BYTES or len(catalog['items']) >= MAX_ARTIFACTS:
                raise ValueError('Private evidence quota reached; export retained evidence before adding more')
            self.admission(tx=tx, kind='evidence_save', byte_count=metadata['size'])
            manifest = dict(policy='pilot_v1', owner_uid=uid, metadata=metadata, state='uploading',
                            generation=generation, chunks=(metadata['size']+CHUNK_BYTES-1)//CHUNK_BYTES,
                            created_at=now, expires_at=now+RETENTION_SECONDS, lease_until=now+LEASE_SECONDS,
                            receipt=None, bundle_id='hosted-'+_hash(artifact_id)+'-'+metadata['sha256']+'.json')
            catalog['bytes'] += metadata['size']
            catalog['items'][key] = artifact_id
            tx.set('evidence_catalog', self.owner_key, catalog)
            tx.set('evidence_manifests', key, manifest)
            tx.set('evidence_requests', request_key, {'artifact_key':key, 'created_at':now})
            return manifest
        manifest = self.store.transaction(reserve)
        if manifest['state'] != 'committed':
            raw = self._download(uid, metadata)
            self._validate(raw, metadata)
            keys = self._chunk_keys(key, manifest)
            for start in range(0, len(keys), 8):
                def write(tx):
                    live = tx.get('evidence_manifests', key)
                    if (not live or live['generation'] != generation or live['state'] != 'uploading'
                            or live['lease_until'] <= self.clock()):
                        raise ValueError('Evidence upload lease expired')
                    for i in range(start, min(start+8, len(keys))):
                        tx.set('evidence_chunks', keys[i], {'raw':raw[i*CHUNK_BYTES:(i+1)*CHUNK_BYTES]})
                self.store.transaction(write)
            # Independent Firestore readback precedes commit and any destructive receipt.
            readback, value = self._read(key, manifest)
            del readback
            from tools.evidence import _identity
            identity = _identity(manifest['bundle_id'], value, raw)
            del raw
            def commit(tx):
                live = tx.get('evidence_manifests', key)
                if (not live or live['generation'] != generation or live['state'] != 'uploading'
                        or live['lease_until'] <= self.clock()):
                    raise ValueError('Evidence upload changed before commit')
                live.update(state='committed', identity=identity)
                tx.set('evidence_manifests', key, live)
                return live
            manifest = self.store.transaction(commit)
        else:
            _, value = self._read(key, manifest)
        return self._receipt(uid, key, manifest, value)

    def saved(self, uid, artifact_id):
        with self._memory_slot():
            return self._saved_cloud(uid,artifact_id)

    def _saved_cloud(self, uid, artifact_id):
        key, manifest = self._manifest(uid, artifact_id)
        if not manifest or manifest['state'] != 'committed' or manifest['expires_at'] <= self.clock():
            return None
        _, value = self._read(key, manifest)
        receipt = manifest.get('receipt') or dict(status='pending_retry', cleanup_requested=None)
        return self._result(manifest['metadata'], value, manifest['bundle_id'], receipt)

    def list_investigations(self, uid, offset=0, limit=20):
        self._owner(uid)
        self.admission(kind='recovery')
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 100:
            raise ValueError('Invalid evidence page')
        catalog = self.store.transaction(lambda tx: tx.get('evidence_catalog', self.owner_key)) or {'items':{}}
        if len(catalog['items']) > MAX_ARTIFACTS:
            raise ValueError('Evidence catalog exceeds pilot limit')
        items = []
        for key in sorted(catalog['items']):
            manifest = self.store.transaction(lambda tx: tx.get('evidence_manifests', key))
            if (manifest and manifest.get('policy') == 'pilot_v1' and manifest.get('owner_uid') == uid
                    and manifest['state'] == 'committed' and manifest['expires_at'] > self.clock()):
                items.append(manifest['identity'])
        return {'investigations':items[offset:offset+limit], 'total_candidates':len(items),
                'invalid':[], 'offset':offset, 'limit':limit, 'next_offset':offset+limit if offset+limit < len(items) else None}

    @contextmanager
    def profile_for(self, uid, bundle_id):
        with self._memory_slot(), self._profile_for(uid,bundle_id) as profile:
            yield profile

    @contextmanager
    def _memory_slot(self):
        if not self._materialization.acquire(blocking=False):
            raise HostedError(429,'Evidence is already being verified; retry shortly')
        try:
            yield
        finally:
            self._materialization.release()

    @contextmanager
    def _profile_for(self, uid, bundle_id):
        self._owner(uid)
        match = re.fullmatch(r'hosted-([0-9a-f]{64})-([0-9a-f]{64})\.json', bundle_id or '')
        if not match:
            raise ValueError('Invalid saved evidence identifier')
        key = self.owner_key + '_' + match.group(1)
        manifest = self.store.transaction(lambda tx: tx.get('evidence_manifests', key))
        if (not manifest or manifest.get('policy') != 'pilot_v1' or manifest.get('owner_uid') != uid
                or manifest['state'] != 'committed' or manifest['expires_at'] <= self.clock()
                or manifest['bundle_id'] != bundle_id):
            raise HostedError(404, 'Evidence not found')
        raw, _ = self._read(key, manifest)
        with tempfile.TemporaryDirectory(prefix='fm-pilot-view-') as directory:
            local = HostedEvidenceLibrary(self.service, durable_root=directory+'/private', artifact_reader=self.reader)
            with local.profile(uid) as profile:
                _atomic(profile['evidence_dir']/bundle_id, raw)
                del raw
                from tools.profile import scoped_evidence_provenance
                with scoped_evidence_provenance(bundle_id, manifest['metadata']['sha256'],
                        manifest['metadata'].get('provenance', {'source':'hosted_device', 'execution_verified':False})):
                    yield profile

    def cleanup_expired(self, *, limit=4):
        """Bounded, idempotent deletion only for this new pilot's expired chunks.

        The expired manifest remains as a tombstone. Claiming deleting inside the
        same transaction used by writers prevents chunks appearing after cleanup.
        A failed physical delete keeps the quota reservation for the next attempt.
        """
        if type(limit) is not int or not 1 <= limit <= 4:
            raise ValueError('Invalid cleanup limit')
        catalog = self.store.transaction(lambda tx: tx.get('evidence_catalog', self.owner_key)) or {'items':{}}
        completed = 0
        for key in list(catalog['items']):
            if completed >= limit:
                break
            def claim(tx):
                m = tx.get('evidence_manifests', key)
                if not m or m.get('policy') != 'pilot_v1' or m.get('owner_uid') != self.uid:
                    return None
                expired = (m['expires_at'] <= self.clock() or
                           (m['state'] == 'uploading' and m['lease_until'] <= self.clock()))
                if not expired and m['state'] != 'deleting':
                    return None
                if m['state'] == 'expired':
                    return None
                if m['state'] != 'deleting':
                    m['delete_reason'] = 'retention' if m['state'] == 'committed' else 'interrupted_upload'
                m['state'] = 'deleting'
                tx.set('evidence_manifests', key, m)
                return m
            manifest = self.store.transaction(claim)
            if manifest is None:
                continue
            self.delete_chunks(self._chunk_keys(key, manifest))
            def release(tx):
                live = tx.get('evidence_manifests', key)
                cat = tx.get('evidence_catalog', self.owner_key)
                if live['state'] == 'deleting' and live['generation'] == manifest['generation']:
                    if key in cat['items']:
                        del cat['items'][key]
                        cat['bytes'] -= live['metadata']['size']
                    live['state'] = 'expired' if live['delete_reason'] == 'retention' else 'aborted'
                    live.pop('identity', None)
                    tx.set('evidence_manifests', key, live)
                    tx.set('evidence_catalog', self.owner_key, cat)
            self.store.transaction(release)
            completed += 1
        return {'removed_pilot_artifacts':completed}


def firestore_chunk_deleter(client):
    def delete(keys):
        if len(keys) > MAX_ARTIFACT // CHUNK_BYTES or any(not re.fullmatch(r'[0-9a-f]{64}_[0-9a-f]{64}_[0-9a-f]{32}_[0-9]+', k) for k in keys):
            raise ValueError('Invalid pilot cleanup keys')
        batch = client.batch()
        for key in keys:
            batch.delete(client.collection('hosted_v1_evidence_chunks').document(key))
        batch.commit()
    return delete
