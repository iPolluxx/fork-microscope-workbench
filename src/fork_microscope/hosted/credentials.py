"""Secret vault, secret-safe values and the shared HTTP transport for hosted mode.

Root integration interface (pinned; stdlib only, cloud SDKs imported lazily):

    vault.put(owner, kind, value) -> ref   # opaque string; never contains the value
    vault.get(ref, *, owner=None) -> str   # controller identity only; owner= enforces binding
    vault.delete(ref) -> None              # idempotent
    InMemoryVault()                        # tests / explicit injection only
    GoogleSecretVault(project_id, transport=None, token_provider=None)
    Secret(value)                          # repr/str/json-safe wrapper; .reveal()
    redact(text, *secrets) -> str
    Transport.request(method, url, headers=None, body=None, timeout=30.0) -> HttpResponse
    Transport.stream(method, url, headers=None, body=None, timeout=60.0) -> StreamResponse
    UrllibTransport(allowed_hosts)         # https only, no redirects, bounded bodies

Deployment split: the public API identity gets only Secret Manager create/addVersion/delete
permissions (put/delete); the private controller identity additionally gets accessor (get).
Nothing here logs; errors never embed secret values or response bodies.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets as _secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Iterator, Mapping, Optional, Protocol

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
OWNER = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
KIND = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
REF = re.compile(r"^sv1:[0-9a-f]{32}$")


class VaultError(Exception):
    """Vault failure. Message is generic and never contains secret material."""


class SecretNotFound(VaultError):
    pass


class TransportError(Exception):
    """Network/policy failure; message never includes headers or bodies."""


class Secret:
    """Holds a sensitive string; repr/str/format/json never reveal it."""
    __slots__ = ("_v",)

    def __init__(self, value: str):
        self._v = value

    def reveal(self) -> str:
        return self._v

    def __repr__(self):
        return "Secret(***)"

    __str__ = __repr__

    def __format__(self, spec):
        return "***"

    def __eq__(self, other):
        return isinstance(other, Secret) and _secrets.compare_digest(self._v.encode(), other._v.encode())

    __hash__ = None

    def __reduce__(self):
        raise TypeError("Secret values are not serializable.")


def redact(text, *secret_values) -> str:
    """Replace every non-trivial secret occurrence (plain, URL-quoted, base64) in text."""
    out = str(text)
    for s in secret_values:
        s = s.reveal() if isinstance(s, Secret) else s
        if not s or len(s) < 4:
            continue
        for form in {s, urllib.parse.quote(s, safe=""), base64.b64encode(s.encode()).decode()}:
            out = out.replace(form, "***")
    return out


def _check(owner, kind):
    if not isinstance(owner, str) or not OWNER.match(owner):
        raise VaultError("Invalid secret owner.")
    if not isinstance(kind, str) or not KIND.match(kind):
        raise VaultError("Invalid secret kind.")


def _digest(owner):
    return hashlib.sha256(owner.encode()).hexdigest()[:40]


class SecretVault(Protocol):
    def put(self, owner: str, kind: str, value: str) -> str: ...
    def get(self, ref: str, *, owner: Optional[str] = None) -> str: ...
    def delete(self, ref: str) -> None: ...


class InMemoryVault:
    """Process-local vault for tests and explicitly injected dev use."""
    durable = False

    def __init__(self):
        self._items = {}
        self._lock = threading.Lock()

    def put(self, owner, kind, value):
        _check(owner, kind)
        if not isinstance(value, str) or not value:
            raise VaultError("Secret value must be a non-empty string.")
        ref = "sv1:" + _secrets.token_hex(16)
        with self._lock:
            self._items[ref] = (owner, kind, value)
        return ref

    def get(self, ref, *, owner=None):
        with self._lock:
            item = self._items.get(ref)
        if item is None or (owner is not None and item[0] != owner):
            raise SecretNotFound("Secret not found.")
        return item[2]

    def delete(self, ref):
        with self._lock:
            self._items.pop(ref, None)

    def __repr__(self):
        return "InMemoryVault(items=%d)" % len(self._items)


@dataclass
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes = b""

    def header(self, name):
        n = name.lower()
        for k, v in self.headers.items():
            if k.lower() == n:
                return v
        return None

    def json(self):
        try:
            value = json.loads(self.body.decode() or "null")
        except ValueError:
            raise TransportError("Response was not valid JSON.") from None
        return value

    def __repr__(self):
        return "HttpResponse(status=%d)" % self.status


@dataclass
class StreamResponse:
    status: int
    headers: Mapping[str, str]
    chunks: Iterator[bytes]
    close: Callable[[], None] = field(default=lambda: None)

    def header(self, name):
        n = name.lower()
        for k, v in self.headers.items():
            if k.lower() == n:
                return v
        return None

    def __repr__(self):
        return "StreamResponse(status=%d)" % self.status


class Transport(Protocol):
    def request(self, method, url, headers=None, body=None, timeout=30.0) -> HttpResponse: ...
    def stream(self, method, url, headers=None, body=None, timeout=60.0) -> StreamResponse: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class UrllibTransport:
    """Real adapter. HTTPS to an explicit host allowlist only; redirects are not followed
    (so Authorization headers can never leak to another origin)."""

    def __init__(self, allowed_hosts, max_response_bytes=MAX_RESPONSE_BYTES, opener=None):
        self.allowed = frozenset(h.lower() for h in allowed_hosts)
        self.max = max_response_bytes
        self._opener = opener or urllib.request.build_opener(_NoRedirect)

    def _open(self, method, url, headers, body, timeout):
        p = urllib.parse.urlsplit(url)
        if p.scheme != "https" or (p.hostname or "").lower() not in self.allowed or p.username or p.password:
            raise TransportError("Request target is not an allowed HTTPS host.")
        if isinstance(body, str):
            body = body.encode()
        req = urllib.request.Request(url, data=body, method=method, headers=dict(headers or {}))
        try:
            return self._opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            return e
        except (urllib.error.URLError, OSError, ValueError):
            raise TransportError("Network request failed.") from None

    def request(self, method, url, headers=None, body=None, timeout=30.0):
        r = self._open(method, url, headers, body, timeout)
        try:
            data = r.read(self.max + 1)
        except OSError:
            raise TransportError("Network read failed.") from None
        finally:
            r.close()
        if len(data) > self.max:
            raise TransportError("Response exceeded the size limit.")
        return HttpResponse(r.status if hasattr(r, "status") else r.code, dict(r.headers.items()), data)

    def stream(self, method, url, headers=None, body=None, timeout=60.0, chunk=1 << 20):
        r = self._open(method, url, headers, body, timeout)

        def gen():
            try:
                while True:
                    b = r.read(chunk)
                    if not b:
                        return
                    yield b
            except OSError:
                raise TransportError("Network read failed.") from None
            finally:
                r.close()
        return StreamResponse(getattr(r, "status", None) or r.code, dict(r.headers.items()), gen(), r.close)


def adc_token_provider(scopes=("https://www.googleapis.com/auth/cloud-platform",)) -> Callable[[], str]:
    """Lazy Application Default Credentials access-token provider (needs google-auth)."""
    state = {}

    def provide():
        import google.auth  # lazy optional dependency
        import google.auth.transport.requests
        if "c" not in state:
            state["c"], _ = google.auth.default(scopes=list(scopes))
        c = state["c"]
        if not c.valid:
            c.refresh(google.auth.transport.requests.Request())
        return c.token
    return provide


class GoogleSecretVault:
    """Secret Manager REST adapter. One secret per value; ref -> secret id 'fm-<hex>'.

    Labels carry sha256(owner)[:40] and kind so get(..., owner=) can enforce binding.
    """
    API = "https://secretmanager.googleapis.com/v1/"
    durable = True

    def __init__(self, project_id, transport: Optional[Transport] = None, token_provider=None):
        if not re.match(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$", project_id or ""):
            raise VaultError("Invalid project id.")
        self.project = project_id
        self.http = transport or UrllibTransport({"secretmanager.googleapis.com"})
        self._token = token_provider or adc_token_provider()

    def __repr__(self):
        return "GoogleSecretVault(project=%s)" % self.project

    def _call(self, method, path, payload=None, ok=(200,)):
        headers = {"Authorization": "Bearer " + self._token(), "Content-Type": "application/json"}
        body = None if payload is None else json.dumps(payload).encode()
        try:
            r = self.http.request(method, self.API + path, headers, body)
        except TransportError:
            raise VaultError("Secret Manager unreachable.") from None
        if r.status == 404:
            raise SecretNotFound("Secret not found.")
        if r.status not in ok:
            raise VaultError("Secret Manager request failed (HTTP %d)." % r.status)
        return r

    @staticmethod
    def _id(ref):
        if not isinstance(ref, str) or not REF.match(ref):
            raise SecretNotFound("Secret not found.")
        return "fm-" + ref[4:]

    def put(self, owner, kind, value):
        _check(owner, kind)
        if not isinstance(value, str) or not value:
            raise VaultError("Secret value must be a non-empty string.")
        token = _secrets.token_hex(16)
        sid = "fm-" + token
        self._call("POST", "projects/%s/secrets?secretId=%s" % (self.project, sid),
                   {"replication": {"automatic": {}}, "labels": {"fm_owner": _digest(owner), "fm_kind": kind.replace("_", "-")}})
        try:
            self._call("POST", "projects/%s/secrets/%s:addVersion" % (self.project, sid),
                       {"payload": {"data": base64.b64encode(value.encode()).decode()}})
        except VaultError:
            try:
                self._call("DELETE", "projects/%s/secrets/%s" % (self.project, sid), ok=(200, 404))
            except VaultError:
                pass
            raise
        return "sv1:" + token

    def get(self, ref, *, owner=None):
        sid = self._id(ref)
        if owner is not None:
            meta = self._call("GET", "projects/%s/secrets/%s" % (self.project, sid)).json()
            if (meta.get("labels") or {}).get("fm_owner") != _digest(owner):
                raise SecretNotFound("Secret not found.")
        r = self._call("GET", "projects/%s/secrets/%s/versions/latest:access" % (self.project, sid)).json()
        try:
            return base64.b64decode(r["payload"]["data"]).decode()
        except (KeyError, ValueError, TypeError):
            raise VaultError("Malformed Secret Manager response.") from None

    def delete(self, ref):
        try:
            self._call("DELETE", "projects/%s/secrets/%s" % (self.project, self._id(ref)), ok=(200, 404))
        except SecretNotFound:
            pass
