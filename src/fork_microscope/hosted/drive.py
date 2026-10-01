"""Google Drive (drive.file, offline) connection, resumable artifact upload and retrieval.

Root integration interface (pinned; stdlib only, injectable Transport, no SDK needed):

Docs backing (durable, not process-local): any object with ``transaction(fn)`` where fn(tx) gets
``tx.get(collection, id) -> dict|None`` and ``tx.set(collection, id, dict)`` (the core store shape).
``MemoryDocs()`` is the process-local test stand-in; DriveAuthorization refuses it unless
``allow_process_local=True``.  Collections: oauth_states, drive_connections, drive_files.

Public API identity (writes secrets, cannot read them):
    DriveAuthorization(docs, vault, config: DriveConfig, client_secret: Secret|callable, transport=None, clock=time.time)
        .begin(owner) -> AuthStart(url, state, binding)  # binding goes in an HttpOnly cookie, state in the URL
        .complete(state, code, binding, expected_owner=None) -> public connection dict
            single-use (state is burned before any check), owner-bound, binding(CSRF)-checked, PKCE S256,
            requires refresh_token and exactly the drive.file scope
        .status(owner) -> {"connected": bool, ...}  (no tokens/refs)
Private controller identity (reads vault):
    DriveController(docs, vault, config, client_secret, transport=None, clock=time.time)
        .access_token(owner) -> Secret          # refresh; invalid_grant => DriveRevoked + connection cleared
        .ensure_folder(owner) -> folder_id
        .register_file(owner, job_id, name, size, sha256, mime="application/json") -> {"file_ref",...}
        .create_upload_session(owner, file_ref) -> UploadCapability(url: Secret, size, sha256, expires_at)
        .upload_status(owner, file_ref) -> {"state": "pending"|"partial"|"complete"|"expired", "received": int}
        .confirm(owner, file_ref) -> {"status": "confirmed", ...}  or raises IntegrityError
        .open_download(owner, file_ref) -> (headers, iterator[bytes])  # streamed, sha256-verified, never stored
        .disconnect(owner) -> {"revoked_remote": bool}
Errors: DriveError (.status) subclasses DriveRevoked(409), NotFound(404, also for other owners),
StateError(400), IntegrityError(502), DriveUnavailable(502).
Public dicts never contain tokens, refresh refs, folder/file Drive ids or session URLs.
Limitation: UploadCapability.url is a bearer capability for ONE file; hand it only to the worker.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets as _secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Callable, Iterator, Optional, Union

from .credentials import Secret, TransportError, UrllibTransport, VaultError

SCOPE = "https://www.googleapis.com/auth/drive.file"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
FILES = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
HOSTS = {"accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com"}
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024  # evidence_io.MAX_IMPORT_BYTES
STATE_TTL = 600
UPLOAD_TTL = 7 * 24 * 3600
SHA = re.compile(r"^[0-9a-f]{64}$")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
JOB = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
MIME = re.compile(r"^[a-z]+/[a-z0-9.+-]+$")
FOLDER_NAME = "Fork Microscope"


class DriveError(Exception):
    status = 400
    def __init__(self, message="Drive request failed."):
        super().__init__(message)
    @property
    def public_message(self):
        return str(self)
class DriveRevoked(DriveError): status = 409
class NotFound(DriveError): status = 404
class StateError(DriveError): status = 400
class IntegrityError(DriveError): status = 502
class DriveUnavailable(DriveError): status = 502


class MemoryDocs:
    """Process-local stand-in with the core store transaction shape (tests / explicit dev)."""
    durable = False

    def __init__(self):
        self._d, self._lock = {}, threading.RLock()

    def transaction(self, fn):
        with self._lock:
            tx = _Tx(self._d)
            return fn(tx)


class _Tx:
    def __init__(self, d): self._d = d
    def get(self, collection, id):
        v = self._d.get((collection, id))
        return None if v is None else dict(v)
    def set(self, collection, id, value): self._d[(collection, id)] = dict(value)


@dataclass(frozen=True)
class DriveConfig:
    client_id: str
    redirect_uri: str

    def __post_init__(self):
        p = urllib.parse.urlsplit(self.redirect_uri)
        local = p.hostname in ("localhost", "127.0.0.1")
        if not self.client_id or p.scheme not in ("https", "http") or (p.scheme == "http" and not local) or p.fragment:
            raise ValueError("Drive config requires a client id and an https (or localhost) redirect URI.")


@dataclass
class AuthStart:
    url: str
    state: str
    binding: str
    def __repr__(self): return "AuthStart(***)"


@dataclass
class UploadCapability:
    url: Secret
    size: int
    sha256: str
    expires_at: float
    def __repr__(self): return "UploadCapability(size=%d)" % self.size


def _h(value): return hashlib.sha256(value.encode()).hexdigest()
def _secret_of(v): return v if isinstance(v, Secret) else (Secret(v()) if callable(v) else Secret(v))


class _Base:
    def __init__(self, docs, vault, config, client_secret, transport=None, clock=time.time):
        self.docs, self.vault, self.cfg, self._cs = docs, vault, config, client_secret
        self.http = transport or UrllibTransport(HOSTS)
        self.clock = clock

    def __repr__(self): return type(self).__name__ + "()"

    def _client_secret(self):
        v = self._cs() if callable(self._cs) else self._cs
        return v.reveal() if isinstance(v, Secret) else v

    def _post_form(self, url, form):
        try:
            return self.http.request("POST", url, {"Content-Type": "application/x-www-form-urlencoded"},
                                     urllib.parse.urlencode(form).encode())
        except TransportError:
            raise DriveUnavailable("Google is unreachable.") from None

    def _conn(self, owner):
        c = self.docs.transaction(lambda tx: tx.get("drive_connections", owner))
        if not c or c.get("owner") != owner or c.get("status") != "connected":
            raise NotFound("Google Drive is not connected.")
        return c

    def _public(self, c):
        if not c or c.get("status") != "connected":
            return {"connected": False}
        return {"connected": True, "scope": SCOPE, "connected_at": c["connected_at"], "has_folder": bool(c.get("folder_id"))}

    def status(self, owner):
        c = self.docs.transaction(lambda tx: tx.get("drive_connections", owner))
        return self._public(c if c and c.get("owner") == owner else None)


class DriveAuthorization(_Base):
    def __init__(self, docs, vault, config, client_secret, transport=None, clock=time.time, allow_process_local=False):
        if not getattr(docs, "durable", True) and not allow_process_local:
            raise ValueError("OAuth state needs durable shared storage; process-local docs are test-only.")
        super().__init__(docs, vault, config, client_secret, transport, clock)

    def begin(self, owner):
        state, binding, verifier = (_secrets.token_urlsafe(32) for _ in range(3))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        rec = dict(owner=owner, binding=_h(binding), verifier=verifier, expires_at=self.clock() + STATE_TTL, used=False)
        self.docs.transaction(lambda tx: tx.set("oauth_states", _h(state), rec))
        q = urllib.parse.urlencode(dict(
            client_id=self.cfg.client_id, redirect_uri=self.cfg.redirect_uri, response_type="code", scope=SCOPE,
            access_type="offline", prompt="consent", include_granted_scopes="false", state=state,
            code_challenge=challenge, code_challenge_method="S256"))
        return AuthStart(AUTH_URL + "?" + q, state, binding)

    def _consume(self, state):
        def fn(tx):
            rec = tx.get("oauth_states", _h(state))
            if rec is None or rec.get("used"):
                return None
            tx.set("oauth_states", _h(state), dict(rec, used=True, verifier=None, binding=None))
            return rec
        return self.docs.transaction(fn)

    def complete(self, state, code, binding, expected_owner=None):
        if not all(isinstance(x, str) and x for x in (state, code, binding)) or len(state) > 256 or len(code) > 2048:
            raise StateError("Invalid authorization response.")
        rec = self._consume(state)  # burned first: any later failure still invalidates the state
        if rec is None or rec["expires_at"] < self.clock():
            raise StateError("Authorization state is invalid, expired or already used.")
        if not hmac.compare_digest(rec["binding"], _h(binding)):
            raise StateError("Authorization state is invalid, expired or already used.")
        owner = rec["owner"]
        if expected_owner is not None and expected_owner != owner:
            raise StateError("Authorization state is invalid, expired or already used.")
        r = self._post_form(TOKEN_URL, dict(
            grant_type="authorization_code", code=code, client_id=self.cfg.client_id,
            client_secret=self._client_secret(), redirect_uri=self.cfg.redirect_uri, code_verifier=rec["verifier"]))
        body = r.json() if r.status == 200 else None
        if not isinstance(body, dict):
            raise StateError("Google rejected the authorization code.")
        refresh = body.get("refresh_token")
        if not refresh or set(str(body.get("scope", "")).split()) != {SCOPE}:
            raise StateError("Google did not grant offline drive.file access only.")
        old = self.docs.transaction(lambda tx: tx.get("drive_connections", owner))
        try:
            ref = self.vault.put(owner, "drive_refresh", refresh)
        except VaultError:
            raise DriveUnavailable("Could not store the Drive credential.") from None
        conn = dict(owner=owner, status="connected", refresh_ref=ref, folder_id=None, connected_at=self.clock())
        self.docs.transaction(lambda tx: tx.set("drive_connections", owner, conn))
        if old and old.get("refresh_ref"):
            try: self.vault.delete(old["refresh_ref"])
            except VaultError: pass
        return self._public(conn)


class DriveController(_Base):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._tokens = {}
        self._tlock = threading.Lock()

    # --- credentials -------------------------------------------------------------------
    def _clear(self, owner, delete_secret=True):
        c = self.docs.transaction(lambda tx: tx.get("drive_connections", owner))
        with self._tlock: self._tokens.pop(owner, None)
        if c:
            self.docs.transaction(lambda tx: tx.set("drive_connections", owner, dict(c, status="revoked", refresh_ref=None, folder_id=None)))
            if delete_secret and c.get("refresh_ref"):
                try: self.vault.delete(c["refresh_ref"])
                except VaultError: pass

    def access_token(self, owner) -> Secret:
        with self._tlock:
            t = self._tokens.get(owner)
        if t and t[1] > self.clock() + 60:
            return t[0]
        c = self._conn(owner)
        try:
            refresh = self.vault.get(c["refresh_ref"], owner=owner)
        except VaultError:
            raise DriveUnavailable("Drive credential unavailable.") from None
        r = self._post_form(TOKEN_URL, dict(grant_type="refresh_token", refresh_token=refresh,
                                            client_id=self.cfg.client_id, client_secret=self._client_secret()))
        body = r.json() if r.status in (200, 400, 401) else None
        if r.status in (400, 401) and isinstance(body, dict) and body.get("error") == "invalid_grant":
            self._clear(owner)
            raise DriveRevoked("Google Drive access was revoked; reconnect Drive.")
        if r.status != 200 or not isinstance(body, dict) or not body.get("access_token"):
            raise DriveUnavailable("Could not refresh Drive access.")
        tok = Secret(body["access_token"])
        with self._tlock:
            self._tokens[owner] = (tok, self.clock() + int(body.get("expires_in", 300)))
        return tok

    def _api(self, owner, method, url, headers=None, body=None, ok=(200,)):
        for attempt in (0, 1):
            h = dict(headers or {}, Authorization="Bearer " + self.access_token(owner).reveal())
            try:
                r = self.http.request(method, url, h, body)
            except TransportError:
                raise DriveUnavailable("Google Drive is unreachable.") from None
            if r.status == 401 and attempt == 0:
                with self._tlock: self._tokens.pop(owner, None)
                continue
            break
        if r.status in ok:
            return r
        if r.status == 401:
            raise DriveRevoked("Google Drive access was revoked; reconnect Drive.")
        if r.status == 404:
            raise NotFound("Drive item not found.")
        raise DriveUnavailable("Drive request failed (HTTP %d)." % r.status)

    # --- folder / files ----------------------------------------------------------------
    def ensure_folder(self, owner):
        c = self._conn(owner)
        if c.get("folder_id"):
            return c["folder_id"]
        import json
        r = self._api(owner, "POST", FILES + "?fields=id", {"Content-Type": "application/json"},
                      json.dumps({"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"}).encode())
        fid = (r.json() or {}).get("id")
        if not isinstance(fid, str) or not fid:
            raise DriveUnavailable("Malformed Drive response.")
        self.docs.transaction(lambda tx: tx.set("drive_connections", owner, dict(tx.get("drive_connections", owner), folder_id=fid)))
        return fid

    def _file(self, owner, file_ref):
        f = self.docs.transaction(lambda tx: tx.get("drive_files", str(file_ref)))
        if not f or f.get("owner") != owner:
            raise NotFound("Artifact not registered.")
        return f

    def _save(self, f):
        self.docs.transaction(lambda tx: tx.set("drive_files", f["file_ref"], f))

    def register_file(self, owner, job_id, name, size, sha256, mime="application/json"):
        if not (isinstance(job_id, str) and JOB.match(job_id) and isinstance(name, str) and NAME.match(name)
                and isinstance(sha256, str) and SHA.match(sha256) and isinstance(mime, str) and MIME.match(mime)
                and isinstance(size, int) and not isinstance(size, bool) and 0 < size <= MAX_ARTIFACT_BYTES):
            raise DriveError("Invalid artifact registration.")
        self._conn(owner)
        ref = "df_" + _secrets.token_hex(16)
        f = dict(file_ref=ref, owner=owner, job_id=job_id, name=name, size=size, sha256=sha256, mime=mime,
                 status="registered", session_ref=None, drive_file_id=None, created_at=self.clock())
        self._save(f)
        return self.public_file(f)

    @staticmethod
    def public_file(f):
        return {k: f[k] for k in ("file_ref", "job_id", "name", "size", "sha256", "mime", "status")}

    def create_upload_session(self, owner, file_ref) -> UploadCapability:
        import json
        f = self._file(owner, file_ref)
        if f["status"] != "registered":
            raise DriveError("Upload session already created for this artifact.")
        folder = self.ensure_folder(owner)
        meta = {"name": f["name"], "parents": [folder], "mimeType": f["mime"],
                "appProperties": {"fm_sha256": f["sha256"], "fm_file_ref": f["file_ref"], "fm_job": f["job_id"]}}
        r = self._api(owner, "POST", UPLOAD + "?uploadType=resumable&fields=id",
                      {"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": f["mime"],
                       "X-Upload-Content-Length": str(f["size"])}, json.dumps(meta).encode())
        loc = r.header("Location") or ""
        p = urllib.parse.urlsplit(loc)
        q = urllib.parse.parse_qs(p.query)
        if p.scheme != "https" or p.hostname != "www.googleapis.com" or p.path != "/upload/drive/v3/files" \
                or p.username or not q.get("upload_id"):
            raise DriveUnavailable("Drive returned an invalid upload session.")
        try:
            sref = self.vault.put(owner, "drive_upload", loc)
        except VaultError:
            raise DriveUnavailable("Could not store the upload session.") from None
        f.update(status="session", session_ref=sref, session_expires_at=self.clock() + UPLOAD_TTL)
        self._save(f)
        return UploadCapability(Secret(loc), f["size"], f["sha256"], f["session_expires_at"])

    def _session_url(self, owner, f):
        if not f.get("session_ref"):
            raise DriveError("No upload session for this artifact.")
        try:
            return self.vault.get(f["session_ref"], owner=owner)
        except VaultError:
            raise DriveUnavailable("Upload session unavailable.") from None

    def upload_status(self, owner, file_ref):
        import json
        f = self._file(owner, file_ref)
        if f["status"] == "confirmed":
            return {"state": "complete", "received": f["size"]}
        url = self._session_url(owner, f)
        try:  # session URL is itself the capability; no bearer is sent
            r = self.http.request("PUT", url, {"Content-Range": "bytes */%d" % f["size"], "Content-Length": "0"}, b"")
        except TransportError:
            raise DriveUnavailable("Google Drive is unreachable.") from None
        if r.status in (200, 201):
            body = r.json()
            fid = body.get("id") if isinstance(body, dict) else None
            if not isinstance(fid, str) or not fid:
                raise DriveUnavailable("Malformed Drive response.")
            if f["status"] == "session":
                f.update(status="uploaded", drive_file_id=fid)
                self._save(f)
            return {"state": "complete", "received": f["size"]}
        if r.status == 308:
            m = re.match(r"^bytes=0-(\d+)$", r.header("Range") or "")
            return {"state": "partial" if m else "pending", "received": int(m.group(1)) + 1 if m else 0}
        if r.status in (404, 410):
            return {"state": "expired", "received": 0}
        raise DriveUnavailable("Upload status failed (HTTP %d)." % r.status)

    def confirm(self, owner, file_ref):
        f = self._file(owner, file_ref)
        if f["status"] == "confirmed":
            return dict(self.public_file(f), verified=True)
        if self.upload_status(owner, file_ref)["state"] != "complete":
            raise DriveError("Upload is not complete.")
        f = self._file(owner, file_ref)
        fid, folder = f["drive_file_id"], self._conn(owner).get("folder_id")
        q = "?fields=id,size,parents,trashed,appProperties,sha256Checksum"
        meta = self._api(owner, "GET", FILES + "/" + urllib.parse.quote(fid, safe="") + q).json() or {}
        ok = (meta.get("id") == fid and str(meta.get("size")) == str(f["size"]) and not meta.get("trashed")
              and folder in (meta.get("parents") or []) and (meta.get("appProperties") or {}).get("fm_file_ref") == f["file_ref"])
        if ok:
            theirs = meta.get("sha256Checksum")
            if theirs is not None:
                ok = hmac.compare_digest(theirs.lower(), f["sha256"])
            else:  # Drive did not report a digest: hash the bytes once (streamed, not stored)
                try:
                    for _ in self._download(owner, f):
                        pass
                except IntegrityError:
                    ok = False
        if not ok:
            f["status"] = "corrupt"
            self._save(f)
            raise IntegrityError("Uploaded artifact failed size/checksum verification.")
        f["status"] = "confirmed"
        self._save(f)
        if f.get("session_ref"):
            try: self.vault.delete(f["session_ref"])
            except VaultError: pass
        return dict(self.public_file(f), verified=True)

    def _download(self, owner, f) -> Iterator[bytes]:
        token = self.access_token(owner).reveal()
        url = FILES + "/" + urllib.parse.quote(f["drive_file_id"], safe="") + "?alt=media"
        try:
            resp = self.http.stream("GET", url, {"Authorization": "Bearer " + token})
        except TransportError:
            raise DriveUnavailable("Google Drive is unreachable.") from None
        if resp.status != 200:
            resp.close()
            raise (NotFound if resp.status == 404 else DriveUnavailable)("Drive download failed.")
        return self._verified(resp, f)

    @staticmethod
    def _verified(resp, f):
        h, n = hashlib.sha256(), 0
        try:
            for chunk in resp.chunks:
                n += len(chunk)
                if n > f["size"]:
                    raise IntegrityError("Drive content exceeds the registered size.")
                h.update(chunk)
                yield chunk
            if n != f["size"] or not hmac.compare_digest(h.hexdigest(), f["sha256"]):
                raise IntegrityError("Drive content failed checksum verification.")
        except TransportError:
            raise DriveUnavailable("Google Drive is unreachable.") from None
        finally:
            resp.close()

    def open_download(self, owner, file_ref):
        f = self._file(owner, file_ref)
        if f["status"] != "confirmed" or not f.get("drive_file_id"):
            raise DriveError("Artifact is not confirmed.")
        gen = self._download(owner, f)
        headers = {"Content-Type": f["mime"], "Content-Length": str(f["size"]), "Cache-Control": "no-store",
                   "Content-Disposition": 'attachment; filename="%s"' % f["name"]}
        return headers, gen

    # --- disconnect --------------------------------------------------------------------
    def disconnect(self, owner):
        c = self.docs.transaction(lambda tx: tx.get("drive_connections", owner))
        if not c or c.get("owner") != owner or c.get("status") != "connected":
            return {"revoked_remote": False}
        remote = False
        try:
            refresh = self.vault.get(c["refresh_ref"], owner=owner)
            remote = self._post_form(REVOKE_URL, {"token": refresh}).status == 200
        except (VaultError, DriveError):
            pass
        self._clear(owner)
        return {"revoked_remote": remote}
