"""Hosted authentication: strict Firebase ID-token verification and invite allowlist.

Root integration interface (pinned):

    authenticator.verify(bearer: str) -> {"uid": str, "email": str}
        raises AuthenticationError (HTTP 401) or NotInvitedError (HTTP 403); both expose
        .status and a generic .public_message. Never returns tokens or claims beyond uid/email.
    FirebaseAuthenticator(project_id, invites, revocation=None, transport=None, clock=time.time,
                          signature_verifier=None, require_revocation_check=True)
    StaticInvites(emails)               # .is_invited(email, uid) -> bool ; swap for Firestore-backed
    IdentityToolkitRevocation(project_id, token_provider, transport=None)
        .check(uid, issued_at) -> raises on revoked/disabled/unknown (fail closed)
    FakeAuthenticator({token: {"uid":..., "email":...}})   # explicit injection ONLY

Checks: RS256 + kid from Google securetoken x509 certs, iss == https://securetoken.google.com/<project>,
aud == <project>, exp/iat/auth_time sanity, sub non-empty, email present and verified, invite allowlist,
and server-side revocation/disabled lookup (Admin-equivalent check_revoked). Any failure denies.
The default signature verifier lazily imports `cryptography`; tests may inject one.
There is no environment/flag based fallback: nothing selects FakeAuthenticator automatically and its
constructor refuses to run when FM_HOSTED_ENV=production.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from typing import Callable, Iterable, Optional

from .credentials import HttpResponse, TransportError, UrllibTransport

CERT_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
SKEW = 60
MAX_TOKEN_BYTES = 8192
PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")


class AuthenticationError(Exception):
    status = 401
    public_message = "Authentication required."


class NotInvitedError(AuthenticationError):
    status = 403
    public_message = "This account is not invited to the hosted beta."


def _b64(part):
    try:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    except ValueError:
        raise AuthenticationError("malformed") from None


def _norm(email):
    return email.strip().lower()


class StaticInvites:
    def __init__(self, emails: Iterable[str]):
        self._emails = frozenset(_norm(e) for e in emails if isinstance(e, str) and e.strip())

    def is_invited(self, email, uid=None) -> bool:
        return isinstance(email, str) and _norm(email) in self._emails

    def __repr__(self):
        return "StaticInvites(count=%d)" % len(self._emails)


class IdentityToolkitRevocation:
    """Admin-equivalent revocation: accounts:lookup -> validSince/disabled. Fails closed."""
    API = "https://identitytoolkit.googleapis.com/v1/projects/%s/accounts:lookup"

    def __init__(self, project_id, token_provider, transport=None):
        self.project = project_id
        self._token = token_provider
        self.http = transport or UrllibTransport({"identitytoolkit.googleapis.com"})

    def check(self, uid, issued_at):
        try:
            r = self.http.request("POST", self.API % self.project,
                                  {"Authorization": "Bearer " + self._token(), "Content-Type": "application/json"},
                                  json.dumps({"localId": [uid]}).encode())
        except TransportError:
            raise AuthenticationError("revocation lookup unavailable") from None
        if r.status != 200:
            raise AuthenticationError("revocation lookup failed")
        users = r.json().get("users") if isinstance(r.json(), dict) else None
        if not users or users[0].get("localId") != uid:
            raise AuthenticationError("unknown user")
        u = users[0]
        if u.get("disabled"):
            raise AuthenticationError("disabled")
        try:
            valid_since = int(u.get("validSince") or 0)
        except (TypeError, ValueError):
            raise AuthenticationError("bad validSince") from None
        if issued_at < valid_since:
            raise AuthenticationError("revoked")


def _rs256_cryptography(cert_pem: bytes, signing_input: bytes, signature: bytes) -> bool:
    from cryptography import x509  # lazy
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    try:
        key = x509.load_pem_x509_certificate(cert_pem).public_key()
        key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


class FirebaseAuthenticator:
    def __init__(self, project_id, invites, revocation=None, transport=None, clock: Callable[[], float] = time.time,
                 signature_verifier=None, require_revocation_check=True):
        if not PROJECT.match(project_id or ""):
            raise ValueError("Invalid Firebase project id.")
        if require_revocation_check and revocation is None:
            raise ValueError("A revocation checker is required (pass require_revocation_check=False only in tests).")
        self.project = project_id
        self.invites = invites
        self.revocation = revocation
        self.http = transport or UrllibTransport({"www.googleapis.com"})
        self.clock = clock
        self._verify_sig = signature_verifier or _rs256_cryptography
        self._certs, self._expires = {}, 0.0
        self._lock = threading.Lock()

    def __repr__(self):
        return "FirebaseAuthenticator(project=%s)" % self.project

    def _keys(self, force=False):
        now = self.clock()
        with self._lock:
            if self._certs and now < self._expires and not force:
                return self._certs
        try:
            r: HttpResponse = self.http.request("GET", CERT_URL)
        except TransportError:
            raise AuthenticationError("cert fetch failed") from None
        data = r.json() if r.status == 200 else None
        if not isinstance(data, dict) or not data:
            raise AuthenticationError("cert fetch failed")
        m = re.search(r"max-age=(\d+)", r.header("Cache-Control") or "")
        ttl = min(int(m.group(1)), 86400) if m else 300
        with self._lock:
            self._certs, self._expires = {k: v.encode() for k, v in data.items() if isinstance(v, str)}, now + ttl
            return self._certs

    def verify(self, bearer):
        if not isinstance(bearer, str) or not bearer or len(bearer.encode()) > MAX_TOKEN_BYTES:
            raise AuthenticationError("missing")
        parts = bearer.split(".")
        if len(parts) != 3 or not all(parts):
            raise AuthenticationError("malformed")
        try:
            header = json.loads(_b64(parts[0]))
            claims = json.loads(_b64(parts[1]))
        except ValueError:
            raise AuthenticationError("malformed") from None
        if not isinstance(header, dict) or not isinstance(claims, dict):
            raise AuthenticationError("malformed")
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise AuthenticationError("alg")
        keys = self._keys()
        cert = keys.get(header["kid"])
        if cert is None:
            cert = self._keys(force=True).get(header["kid"])
        if cert is None:
            raise AuthenticationError("kid")
        if not self._verify_sig(cert, (parts[0] + "." + parts[1]).encode(), _b64(parts[2])):
            raise AuthenticationError("signature")
        now = self.clock()
        num = lambda k: claims.get(k) if isinstance(claims.get(k), (int, float)) and not isinstance(claims.get(k), bool) else None
        exp, iat, auth_time = num("exp"), num("iat"), num("auth_time")
        if None in (exp, iat, auth_time) or any(not __import__("math").isfinite(v) for v in (exp, iat, auth_time)):
            raise AuthenticationError("times")
        if exp <= now - 0 or iat > now + SKEW or auth_time > now + SKEW:
            raise AuthenticationError("expired")
        if claims.get("aud") != self.project or claims.get("iss") != "https://securetoken.google.com/" + self.project:
            raise AuthenticationError("audience/issuer")
        uid = claims.get("sub")
        if not isinstance(uid, str) or not 0 < len(uid) <= 128 or uid != claims.get("user_id", uid):
            raise AuthenticationError("sub")
        email = claims.get("email")
        if not isinstance(email, str) or "@" not in email or claims.get("email_verified") is not True:
            raise AuthenticationError("email")
        if self.revocation is not None:
            self.revocation.check(uid, int(auth_time))
        if not self.invites.is_invited(email, uid):
            raise NotInvitedError("not invited")
        return {"uid": uid, "email": _norm(email)}


class FakeAuthenticator:
    """Explicitly injected test authenticator: token -> identity. Never auto-selected."""

    def __init__(self, users: dict):
        if os.environ.get("FM_HOSTED_ENV", "").lower() == "production":
            raise RuntimeError("FakeAuthenticator is forbidden in production.")
        self._users = {t: {"uid": u["uid"], "email": u["email"]} for t, u in users.items()}

    def verify(self, bearer):
        u = self._users.get(bearer) if isinstance(bearer, str) else None
        if u is None:
            raise AuthenticationError("unknown token")
        return dict(u)

    def __repr__(self):
        return "FakeAuthenticator(users=%d)" % len(self._users)
