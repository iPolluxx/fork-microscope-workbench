"""Persistent, explicitly configured one-account OAuth for the managed pilot.

No clients are discovered or fetched, and no credentials or grants are created at
import or construction. The store supplies transaction(fn), whose transaction has
get(collection, key)/set(collection, key, value), like the existing FirestoreStore.
Use its hosted_v1 namespace: this module accesses only mcp_oauth_* collections.

All bearer credentials and browser/flow bindings are stored as SHA-256 hashes.
Original client state stays in the bound HttpOnly browser cookie; only its hash is
stored. Firebase verification and authoritative account revocation are mandatory
injected server functions. Error messages and repr never include tokens or claims.

Before production activation, independently verify the public approved CIMD
document and the connection page's actual client/redirect mode. get_client uses
that pinned policy; it does not fetch or certify live client metadata itself.
"""
from __future__ import annotations

import asyncio
import base64
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import re
import secrets
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastmcp.server.auth import AccessToken, OAuthProvider
from fastmcp.server.auth.auth import TokenHandler
from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.provider import AuthorizationCode, AuthorizeError, RefreshToken, TokenError
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from fork_microscope.hosted.credentials import OWNER


SCOPE = "fork:manage"
COOKIE = "__Host-fm_mcp_oauth"
_CONTEXT = ContextVar("fork_oauth_authorization", default=None)
_FIXED_CLIENT = "https://chatgpt.com/oauth/client.json"
_FIXED_REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"
_TOKEN = re.compile(r"^fm_(fl|br|ac|at|rt)_[A-Za-z0-9_-]{43}$")
_COLLECTIONS = {kind: "mcp_oauth_" + kind for kind in ("flows", "codes", "grants", "access", "refresh", "limits")}
_POLICY = "single_account_oauth_pilot_v1"
_RETENTION_SECONDS = 30 * 86400


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new(kind):
    return "fm_" + kind + "_" + secrets.token_urlsafe(32)


def _credential(value, kind):
    return isinstance(value, str) and bool(_TOKEN.fullmatch(value)) and value.startswith("fm_" + kind + "_")


def _integer(value):
    return type(value) is int and 0 <= value < 2**53


def _public_origin(value):
    try:
        p = urlsplit(value)
        if (not isinstance(value, str) or not value.isascii() or any(ord(c) <= 32 for c in value)
                or p.scheme != "https" or not p.hostname or p.username is not None or p.password is not None
                or p.port not in (None, 443) or p.path or p.query or p.fragment or "?" in value or "#" in value
                or "\\" in value or not re.fullmatch(r"https://[a-z0-9.-]+(?::443)?", value)):
            raise ValueError()
    except Exception:
        raise ValueError("Configure an exact HTTPS OAuth issuer origin") from None
    return value


@dataclass(frozen=True, repr=False)
class PilotOAuthConfig:
    issuer: str
    resource: str
    pilot_uid: str
    client_id: str
    redirect_uri: str
    access_ttl: int = 300
    refresh_ttl: int = 86400
    grant_ttl: int = 7 * 86400
    flow_ttl: int = 600
    code_ttl: int = 120
    authorizations_per_hour: int = 12
    refreshes_per_hour: int = 60

    def __post_init__(self):
        _public_origin(self.issuer)
        if (self.resource != self.issuer + "/mcp"
                or not isinstance(self.pilot_uid, str) or not OWNER.fullmatch(self.pilot_uid)
                or self.client_id != _FIXED_CLIENT or self.redirect_uri != _FIXED_REDIRECT):
            raise ValueError("Configure the verified pilot UID and approved exact ChatGPT client, callback and MCP resource")
        for name, lower, upper in (("access_ttl", 60, 600), ("refresh_ttl", 600, 7*86400),
                ("grant_ttl", 600, 30*86400), ("flow_ttl", 60, 600), ("code_ttl", 30, 120),
                ("authorizations_per_hour", 1, 60), ("refreshes_per_hour", 1, 120)):
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError("Configure bounded OAuth lifetimes and request limits")
        if self.refresh_ttl > self.grant_ttl:
            raise ValueError("Refresh lifetime cannot exceed the grant lifetime")

    def __repr__(self):
        return "PilotOAuthConfig(configured=True)"


def verified_firebase_identity(authenticator, bearer):
    """Reuse existing signature/invite/revocation verification before reading claims.

    The returned auth_time is retained for later IdentityToolkit validSince checks;
    an OAuth refresh never replaces it with a newer timestamp.
    """
    try:
        identity = authenticator.verify(bearer)
        payload = bearer.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if (not isinstance(identity, dict) or claims.get("sub") != identity.get("uid")
                or not _integer(claims.get("auth_time"))):
            raise ValueError()
        return {"uid": identity["uid"], "auth_time": claims["auth_time"]}
    except Exception:
        raise ValueError("Firebase account authentication failed") from None


class PilotOAuthProvider(OAuthProvider):
    """Opaque-token provider: a token is verified by its owner-bound durable record.

    firebase_verify(ID_token) -> {uid, auth_time}, after signature/invite/revocation
    checks. check_account_access(uid, auth_time) -> literal True only while the
    configured Firebase account is enabled, invited and not revoked since login.
    Both callbacks are synchronous trusted server functions; never log bearers.
    recovery_admission may charge a bounded recovery allowance for refresh of an
    already valid grant; without it, refresh uses the ordinary admission callback.
    """
    def __init__(self, config, *, store, firebase_verify, check_account_access, firebase_config,
                 admission=None, recovery_admission=None, clock=time.time):
        if (not isinstance(config, PilotOAuthConfig) or not callable(getattr(store, "transaction", None))
                or not callable(firebase_verify) or not callable(check_account_access) or not callable(clock)
                or (admission is not None and not callable(admission))
                or (recovery_admission is not None and not callable(recovery_admission))):
            raise ValueError("Configured pilot identity, persistent store and authoritative Firebase checks are required")
        if (not isinstance(firebase_config, dict)
                or not {"apiKey","authDomain","projectId","appId"} <= set(firebase_config)
                or any(not isinstance(firebase_config[k], str) or not 1 <= len(firebase_config[k]) <= 200
                       for k in ("apiKey","authDomain","projectId","appId"))
                or not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", firebase_config["projectId"])
                or firebase_config["authDomain"] != firebase_config["projectId"] + ".firebaseapp.com"):
            raise ValueError("Configure the existing Firebase public web application metadata")
        self.firebase_config = {k:firebase_config[k] for k in ("apiKey","authDomain","projectId","appId")}
        self.admission = admission
        self.recovery_admission = recovery_admission
        self.config, self.store = config, store
        self.firebase_verify, self.check_account_access, self.clock = firebase_verify, check_account_access, clock
        super().__init__(base_url=config.issuer, issuer_url=config.issuer,
            resource_base_url=config.issuer, required_scopes=[SCOPE],
            client_registration_options=ClientRegistrationOptions(enabled=False, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
            revocation_options=RevocationOptions(enabled=True))

    def __repr__(self):
        return "PilotOAuthProvider(single_account=True, persistent_state_required=True)"

    def _now(self):
        value = self.clock()
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("OAuth clock unavailable")
        return int(value)

    def _row(self, **fields):
        return dict(fields, policy=_POLICY, owner_uid=self.config.pilot_uid,
            delete_after=datetime.fromtimestamp(self._now() + _RETENTION_SECONDS, timezone.utc))

    @staticmethod
    def _get(tx, kind, key):
        return tx.get(_COLLECTIONS[kind], key)

    @staticmethod
    def _set(tx, kind, key, value):
        tx.set(_COLLECTIONS[kind], key, value)

    def _limit(self, tx, key, now, limit):
        row = self._get(tx, "limits", key) or {}
        hour = now // 3600
        count = row.get("count", 0) if row.get("hour") == hour else 0
        if type(count) is not int or count >= limit:
            return False
        self._set(tx, "limits", key, self._row(hour=hour, count=count+1))
        return True

    def _client(self, client):
        return isinstance(client, OAuthClientInformationFull) and client.client_id == self.config.client_id

    def _account(self, auth_time):
        try:
            return (_integer(auth_time) and auth_time <= self._now()
                    and self.check_account_access(self.config.pilot_uid, auth_time) is True)
        except Exception:
            return False

    def _admit(self, *, recovery=False):
        """Optional shared pilot admission; rejection occurs before any OAuth write.

        The deployment supplies lambda: budget.admit(kind='oauth'). It returns
        None/True on success and raises on denial; explicit False also denies.
        """
        callback = self.recovery_admission if recovery and self.recovery_admission is not None else self.admission
        if callback is not None:
            result = callback()
            if result is not None and result is not True:
                raise ValueError("OAuth admissions are paused")

    def _grant_valid(self, grant, now):
        c = self.config
        return (isinstance(grant, dict) and grant.get("issuer") == c.issuer
            and grant.get("subject") == c.pilot_uid and grant.get("owner_uid") == c.pilot_uid
            and grant.get("client_id") == c.client_id and grant.get("resource") == c.resource
            and grant.get("scopes") == [SCOPE] and grant.get("revoked") is False
            and _integer(grant.get("expires_at")) and grant["expires_at"] > now)

    async def get_client(self, client_id):
        # A pinned CIMD allowlist, not an arbitrary URL fetch or DCR fallback.
        if client_id != self.config.client_id:
            return None
        return OAuthClientInformationFull(client_id=self.config.client_id, client_name="ChatGPT",
            redirect_uris=[self.config.redirect_uri], scope=SCOPE, token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"], response_types=["code"])

    async def register_client(self, client_info):
        raise ValueError("Dynamic client registration is disabled")

    async def authorize(self, client, params):
        context = _CONTEXT.get()
        if (not self._client(client) or not isinstance(context, dict)
                or params.resource != self.config.resource or params.scopes != [SCOPE]
                or str(params.redirect_uri) != self.config.redirect_uri
                or not params.redirect_uri_provided_explicitly
                or not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.code_challenge or "")
                or (params.state is not None and (not isinstance(params.state, str) or len(params.state) > 1024
                    or any(ord(c) < 32 for c in params.state)))):
            raise AuthorizeError("invalid_request", "The configured client, resource, scope and S256 request are required")
        flow, now = _new("fl"), self._now()
        record = self._row(client_id=client.client_id, resource=self.config.resource,
            redirect_uri=self.config.redirect_uri, code_challenge=params.code_challenge,
            state_hash=_sha(params.state or ""), state_present=params.state is not None,
            browser_hash=_sha(context["browser"]), expires_at=now+self.config.flow_ttl, used=False)
        def write(tx):
            if not self._limit(tx, "authorization", now, self.config.authorizations_per_hour):
                return False
            if self._get(tx, "flows", _sha(flow)) is not None:
                return False
            self._set(tx, "flows", _sha(flow), record)
            return True
        try:
            await asyncio.to_thread(self._admit)
            if not await asyncio.to_thread(self.store.transaction, write):
                raise ValueError()
        except Exception:
            raise AuthorizeError("temporarily_unavailable", "Authorization is temporarily unavailable") from None
        context.update(state=params.state, flow=flow)
        return self.config.issuer + "/oauth/consent?" + urlencode({"flow": flow})

    def _cookie(self, context):
        value = json.dumps({"browser": context["browser"], "state": context["state"]}, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    def _binding(self, request):
        try:
            raw = request.cookies.get(COOKIE, "")
            if not 1 <= len(raw) <= 3000:
                raise ValueError()
            value = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
            if (not isinstance(value, dict) or set(value) != {"browser", "state"}
                    or not _credential(value["browser"], "br")
                    or (value["state"] is not None and not isinstance(value["state"], str))):
                raise ValueError()
            return value
        except Exception:
            raise ValueError("Authorization browser binding is invalid") from None

    def _flow_valid(self, row, binding, now):
        return (isinstance(row, dict) and row.get("client_id") == self.config.client_id
            and row.get("resource") == self.config.resource and row.get("redirect_uri") == self.config.redirect_uri
            and row.get("used") is False and _integer(row.get("expires_at")) and row["expires_at"] > now
            and hmac.compare_digest(row.get("browser_hash", ""), _sha(binding["browser"]))
            and row.get("state_present") == (binding["state"] is not None)
            and hmac.compare_digest(row.get("state_hash", ""), _sha(binding["state"] or "")))

    def _redirect(self, *, state, code=None, error=None):
        fields = {"iss": self.config.issuer}
        if state is not None:
            fields["state"] = state
        if code is not None:
            fields["code"] = code
        if error is not None:
            fields["error"] = error
        return self.config.redirect_uri + "?" + urlencode(fields)

    def _consent(self, flow, binding, approve, bearer):
        now = self._now()
        if not _credential(flow, "fl"):
            raise ValueError("Authorization request unavailable")
        identity = None
        if approve:
            if not isinstance(bearer, str) or not 1 <= len(bearer) <= 8192:
                raise ValueError("Firebase sign-in is required")
            try:
                identity = self.firebase_verify(bearer)
                if (not isinstance(identity, dict) or identity.get("uid") != self.config.pilot_uid
                        or not self._account(identity.get("auth_time"))):
                    raise ValueError()
            except Exception:
                raise ValueError("The configured pilot account must sign in") from None
            self._admit()
        code = _new("ac") if approve else None
        def commit(tx):
            row = self._get(tx, "flows", _sha(flow))
            if not self._flow_valid(row, binding, now):
                return False
            if approve:
                if self._get(tx, "codes", _sha(code)) is not None:
                    return False
                record = self._row(client_id=self.config.client_id, resource=self.config.resource,
                    redirect_uri=self.config.redirect_uri, code_challenge=row["code_challenge"],
                    subject=self.config.pilot_uid, auth_time=identity["auth_time"], used=False,
                    expires_at=now+self.config.code_ttl, grant_id=None)
                self._set(tx, "codes", _sha(code), record)
            self._set(tx, "flows", _sha(flow), dict(row, used=True))
            return True
        try:
            if not self.store.transaction(commit):
                raise ValueError()
        except Exception:
            raise ValueError("Authorization request unavailable or already used") from None
        return self._redirect(state=binding["state"], code=code, error=None if approve else "access_denied")

    async def load_authorization_code(self, client, authorization_code):
        if not self._client(client) or not _credential(authorization_code, "ac"):
            return None
        try:
            row = await asyncio.to_thread(self.store.transaction, lambda tx:self._get(tx, "codes", _sha(authorization_code)))
            if (not isinstance(row, dict) or row.get("client_id") != self.config.client_id
                    or row.get("resource") != self.config.resource or row.get("subject") != self.config.pilot_uid
                    or row.get("redirect_uri") != self.config.redirect_uri or row.get("expires_at", 0) <= self._now()):
                return None
            return AuthorizationCode(code=authorization_code, client_id=self.config.client_id,
                scopes=[SCOPE], expires_at=row["expires_at"], code_challenge=row["code_challenge"],
                redirect_uri=self.config.redirect_uri, redirect_uri_provided_explicitly=True,
                resource=self.config.resource, subject=self.config.pilot_uid)
        except Exception:
            return None

    def _tokens(self, tx, grant_id, grant, access, refresh, now):
        access_exp = min(now+self.config.access_ttl, grant["expires_at"])
        refresh_exp = min(now+self.config.refresh_ttl, grant["expires_at"])
        self._set(tx, "access", _sha(access), self._row(grant_id=grant_id, issued_at=now, expires_at=access_exp))
        self._set(tx, "refresh", _sha(refresh), self._row(grant_id=grant_id, issued_at=now,
            expires_at=refresh_exp, used=False))
        self._set(tx, "grants", grant_id, dict(grant, active_refresh_hash=_sha(refresh)))
        return OAuthToken(access_token=access, refresh_token=refresh, expires_in=access_exp-now, scope=SCOPE)

    async def exchange_authorization_code(self, client, authorization_code):
        if not self._client(client) or not isinstance(authorization_code, AuthorizationCode):
            raise TokenError("invalid_grant", "Authorization cannot be exchanged")
        code = authorization_code.code
        if not _credential(code, "ac"):
            raise TokenError("invalid_grant", "Authorization cannot be exchanged")
        try:
            await asyncio.to_thread(self._admit)
            row = await asyncio.to_thread(self.store.transaction, lambda tx:self._get(tx, "codes", _sha(code)))
            if not isinstance(row, dict) or not await asyncio.to_thread(self._account, row.get("auth_time")):
                raise ValueError()
            access, refresh, gid, now = _new("at"), _new("rt"), secrets.token_hex(32), self._now()
            def exchange(tx):
                current = self._get(tx, "codes", _sha(code))
                if (current != row or current.get("expires_at", 0) <= now
                        or current.get("client_id") != self.config.client_id
                        or current.get("resource") != self.config.resource
                        or current.get("subject") != self.config.pilot_uid):
                    return None
                if current.get("used") is True:
                    grant = self._get(tx, "grants", current.get("grant_id", ""))
                    if grant:
                        self._set(tx, "grants", current["grant_id"], dict(grant, revoked=True))
                    return None
                if (self._get(tx, "access", _sha(access)) or self._get(tx, "refresh", _sha(refresh))
                        or self._get(tx, "grants", gid)):
                    return None
                grant = self._row(issuer=self.config.issuer, subject=self.config.pilot_uid,
                    owner_uid=self.config.pilot_uid, client_id=self.config.client_id,
                    resource=self.config.resource, scopes=[SCOPE], auth_time=current["auth_time"],
                    issued_at=now, expires_at=now+self.config.grant_ttl, revoked=False)
                self._set(tx, "codes", _sha(code), dict(current, used=True, grant_id=gid))
                return self._tokens(tx, gid, grant, access, refresh, now)
            result = await asyncio.to_thread(self.store.transaction, exchange)
            if result is None:
                raise ValueError()
            return result
        except Exception:
            raise TokenError("invalid_grant", "Authorization cannot be exchanged") from None

    def _read_access(self, bearer):
        if not _credential(bearer, "at"):
            return None
        try:
            def read(tx):
                record = self._get(tx, "access", _sha(bearer))
                grant = self._get(tx, "grants", record.get("grant_id", "")) if isinstance(record, dict) else None
                return record, grant
            record, grant = self.store.transaction(read)
            now = self._now()
            if (not record or not self._grant_valid(grant, now) or not _integer(record.get("expires_at"))
                    or record["expires_at"] <= now or not self._account(grant.get("auth_time"))):
                return None
            return AccessToken(token=bearer, client_id=self.config.client_id, scopes=[SCOPE],
                expires_at=record["expires_at"], subject=self.config.pilot_uid, resource=self.config.resource,
                claims={"iss":self.config.issuer, "sub":self.config.pilot_uid,"aud":self.config.resource,
                    "exp":record["expires_at"],"iat":record["issued_at"],"auth_time":grant["auth_time"],
                    "grant_id":record["grant_id"]})
        except Exception:
            return None

    async def load_access_token(self, token):
        return await asyncio.to_thread(self._read_access, token)

    def access_check(self, *, issuer, subject, hosted_uid, token):
        """Authoritative per-invocation callback for hosted_pilot's owner resolver."""
        if (issuer != self.config.issuer or subject != self.config.pilot_uid or hosted_uid != self.config.pilot_uid
                or not isinstance(token, AccessToken)):
            return False
        verified = self._read_access(token.token)
        return verified is not None and verified == token

    async def load_refresh_token(self, client, refresh_token):
        if not self._client(client) or not _credential(refresh_token, "rt"):
            return None
        try:
            def read(tx):
                row = self._get(tx, "refresh", _sha(refresh_token))
                grant = self._get(tx, "grants", row.get("grant_id", "")) if isinstance(row, dict) else None
                return row, grant
            row, grant = await asyncio.to_thread(self.store.transaction, read)
            if (not row or not self._grant_valid(grant, self._now()) or row.get("expires_at", 0) <= self._now()
                    or not await asyncio.to_thread(self._account, grant.get("auth_time"))):
                return None
            return RefreshToken(token=refresh_token, client_id=self.config.client_id, scopes=[SCOPE],
                expires_at=row["expires_at"], subject=self.config.pilot_uid, resource=self.config.resource)
        except Exception:
            return None

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        if (not self._client(client) or not isinstance(refresh_token, RefreshToken)
                or not _credential(refresh_token.token, "rt") or scopes != [SCOPE]):
            raise TokenError("invalid_grant", "Refresh grant cannot be exchanged")
        raw = refresh_token.token
        try:
            if await self.load_refresh_token(client, raw) is None:
                raise ValueError()
            # Only a still-valid existing grant can consume the bounded recovery
            # reserve. New authorization/code exchange always uses new-work admission.
            await asyncio.to_thread(self._admit, recovery=True)
            access, refresh, now = _new("at"), _new("rt"), self._now()
            def exchange(tx):
                row = self._get(tx, "refresh", _sha(raw))
                grant = self._get(tx, "grants", row.get("grant_id", "")) if isinstance(row, dict) else None
                if not row or not self._grant_valid(grant, now) or row.get("expires_at", 0) <= now:
                    return None
                if row.get("used") is True or grant.get("active_refresh_hash") != _sha(raw):
                    self._set(tx, "grants", row["grant_id"], dict(grant, revoked=True))
                    return None
                if (self._get(tx, "access", _sha(access)) or self._get(tx, "refresh", _sha(refresh))
                        or not self._limit(tx, "refresh", now, self.config.refreshes_per_hour)):
                    return None
                self._set(tx, "refresh", _sha(raw), dict(row, used=True))
                return self._tokens(tx, row["grant_id"], grant, access, refresh, now)
            result = await asyncio.to_thread(self.store.transaction, exchange)
            if result is None:
                raise ValueError()
            return result
        except Exception:
            raise TokenError("invalid_grant", "Refresh grant cannot be exchanged") from None

    async def revoke_token(self, token):
        raw = getattr(token, "token", None)
        kind = "access" if _credential(raw, "at") else "refresh" if _credential(raw, "rt") else None
        if kind is None or getattr(token, "client_id", None) != self.config.client_id:
            return
        def revoke(tx):
            row = self._get(tx, kind, _sha(raw))
            grant = self._get(tx, "grants", row.get("grant_id", "")) if isinstance(row, dict) else None
            if grant and grant.get("client_id") == self.config.client_id:
                self._set(tx, "grants", row["grant_id"], dict(grant, revoked=True))
        try:
            await asyncio.to_thread(self.store.transaction, revoke)
        except Exception:
            raise ValueError("Grant revocation temporarily unavailable") from None

    async def _authorize_route(self, request):
        context = {"browser": _new("br"), "state": None}
        marker = _CONTEXT.set(context)
        try:
            request = await self._bounded_request(request)
            response = await AuthorizationHandler(self).handle(request)
            location = response.headers.get("location")
            if location:
                parsed = urlsplit(location)
                if urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")) == self.config.redirect_uri:
                    # Add exact issuer on every client-facing success/error redirect.
                    fields = [(k,v) for k,v in parse_qsl(parsed.query, keep_blank_values=True) if k != "iss"]
                    if any(k == "error" for k,v in fields):
                        fields = [(k, "Authorization request rejected" if k == "error_description" else v) for k,v in fields]
                    fields.append(("iss", self.config.issuer))
                    response.headers["location"] = self.config.redirect_uri + "?" + urlencode(fields)
                elif context.get("flow") and location == self.config.issuer + "/oauth/consent?" + urlencode({"flow":context["flow"]}):
                    response.set_cookie(COOKIE, self._cookie(context), secure=True, httponly=True,
                        samesite="lax", max_age=self.config.flow_ttl, path="/")
                else:
                    return self._error()
            self._secure_headers(response)
            return self._sanitize_protocol_error(response)
        except Exception:
            return self._error()
        finally:
            _CONTEXT.reset(marker)

    async def _bounded_request(self, request):
        if request.method == "GET":
            raw = request.scope.get("query_string", b"")
        else:
            if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
                raise ValueError()
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 8192:
                    raise ValueError()
            raw = bytes(raw)
        if len(raw) > 8192:
            raise ValueError()
        pairs = parse_qsl(raw.decode("ascii"), keep_blank_values=True, max_num_fields=20)
        if len({k for k,v in pairs}) != len(pairs):
            raise ValueError()
        values = dict(pairs)
        if "resource" in values and values["resource"] != self.config.resource:
            raise ValueError()
        if request.url.path == "/token" and values.get("resource") != self.config.resource:
            raise ValueError()
        if request.method == "POST":
            async def receive():
                return {"type":"http.request", "body":raw, "more_body":False}
            request = Request(request.scope, receive)
        return request

    def _sanitize_protocol_error(self, response):
        if response.status_code < 400:
            return response
        # SDK validation messages can reflect bearer/form contents. Return the
        # protocol error code only, with a fixed description and no raw inputs.
        try:
            error = json.loads(response.body).get("error")
        except Exception:
            error = None
        if error not in {"invalid_request", "invalid_client", "invalid_grant", "unauthorized_client",
                         "unsupported_grant_type", "invalid_scope", "invalid_target", "access_denied"}:
            error = "invalid_request"
        return self._secure_headers(JSONResponse({"error":error,"error_description":"Authorization request rejected"},
            status_code=401 if error == "invalid_client" else 400))

    async def _token_route(self, request):
        try:
            request = await self._bounded_request(request)
            handler = TokenHandler(self, ClientAuthenticator(self))
            return self._secure_headers(self._sanitize_protocol_error(await handler.handle(request)))
        except Exception:
            return self._error()

    async def _revoke_route(self, request):
        try:
            request = await self._bounded_request(request)
            form = await request.form()
            if (set(form) - {"client_id", "token", "token_type_hint"}
                    or form.get("client_id") != self.config.client_id
                    or not isinstance(form.get("token"), str) or not 1 <= len(form["token"]) <= 512
                    or form.get("token_type_hint") not in (None, "access_token", "refresh_token")):
                raise ValueError()
            raw = form["token"]
            # The installed SDK's RevocationRequest requires client_secret even
            # for a registered public client. Implement this narrow RFC 7009
            # endpoint explicitly: no dummy secret and no new client credentials.
            if _credential(raw, "at") or _credential(raw, "rt"):
                token = RefreshToken(token=raw, client_id=self.config.client_id, scopes=[SCOPE])
                try:
                    await self.revoke_token(token)
                except Exception:
                    return self._error(503)
            return self._secure_headers(Response(status_code=200))
        except Exception:
            return self._error()

    @staticmethod
    def _secure_headers(response):
        response.headers.update({"Cache-Control":"no-store", "Pragma":"no-cache", "Referrer-Policy":"no-referrer",
            "X-Content-Type-Options":"nosniff", "X-Frame-Options":"DENY"})
        return response

    def _error(self, status=400):
        return self._secure_headers(JSONResponse({"error":"invalid_request", "error_description":"Authorization request unavailable"}, status_code=status))

    async def _consent_route(self, request):
        try:
            binding = self._binding(request)
            if request.method == "GET":
                flow = request.query_params.get("flow")
                if not _credential(flow, "fl"):
                    raise ValueError()
                row = await asyncio.to_thread(self.store.transaction, lambda tx:self._get(tx, "flows", _sha(flow)))
                if not self._flow_valid(row, binding, self._now()):
                    raise ValueError()
                nonce = secrets.token_urlsafe(24)
                response = HTMLResponse(self.consent_html(flow, nonce, row["expires_at"]))
                response.headers["Content-Security-Policy"] = ("default-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
                    "form-action 'self'; script-src 'nonce-"+nonce+"' https://www.gstatic.com https://apis.google.com; style-src 'nonce-"+nonce+"'; "
                    "connect-src 'self' https://identitytoolkit.googleapis.com https://securetoken.googleapis.com https://www.googleapis.com; "
                    "frame-src https://"+self.firebase_config["authDomain"]+" https://accounts.google.com")
                return self._secure_headers(response)
            if request.headers.get("origin") != self.config.issuer or request.headers.get("content-type", "").split(";")[0] != "application/json":
                raise ValueError()
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 4096:
                    raise ValueError()
            body = json.loads(raw)
            if not isinstance(body, dict) or set(body) != {"flow", "approve"} or type(body["approve"]) is not bool:
                raise ValueError()
            header = request.headers.get("authorization", "")
            bearer = header[7:] if header.startswith("Bearer ") else None
            url = await asyncio.to_thread(self._consent, body["flow"], binding, body["approve"], bearer)
            response = JSONResponse({"redirect_url":url})
            response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")
            return self._secure_headers(response)
        except Exception:
            return self._error()

    def consent_html(self, flow, nonce, expires_at=None):
        # Flow is a bounded opaque nonce, never a token or account identifier.
        script = """
const flow=FLOW,expiresAt=EXPIRES;const status=document.getElementById('status');let user=null;
const approve=document.getElementById('approve'),signin=document.getElementById('signin'),deny=document.getElementById('deny');
let authReady=false,signingIn=false,finishing=false,attempt=0;
function expired(){if(Date.now()/1000<expiresAt)return false;user=null;approve.disabled=true;signin.disabled=true;
 status.textContent='This connection request expired. Return to ChatGPT and restart Connect.';return true;}
async function finish(allow){if(finishing||expired())return;finishing=true;attempt++;signingIn=false;
 approve.disabled=true;signin.disabled=true;deny.disabled=true;let bearer=null;try{
 if(allow){if(!user)throw Error('Sign in first.');bearer=await user.getIdToken(true);}
 const response=await fetch('/oauth/consent',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json',...(bearer?{Authorization:'Bearer '+bearer}:{})},body:JSON.stringify({flow,approve:allow})});
 bearer=null;const result=await response.json();if(!response.ok)throw Error('Authorization could not be completed. Restart the connection.');
 const url=new URL(result.redirect_url);if(url.origin!=='https://chatgpt.com'||url.pathname!=='/connector_platform_oauth_redirect')throw Error('Unexpected return address.');location.assign(url.href);
 }catch{status.textContent='Authorization could not be completed. Return to ChatGPT and restart Connect.';user=null;deny.disabled=false;}
 finally{bearer=null;finishing=false;}}
deny.onclick=()=>finish(false);approve.onclick=()=>finish(true);
window.addEventListener('pageshow',event=>{if(event.persisted){attempt++;user=null;signingIn=false;finishing=false;
 approve.disabled=true;signin.disabled=!authReady;deny.disabled=false;
 status.textContent='Sign in again with the approved Google account, then choose Allow.';expired();}});
try{const configuration=FIREBASECONFIG;
 const [app,sdk]=await Promise.all([import('https://www.gstatic.com/firebasejs/12.3.0/firebase-app.js'),import('https://www.gstatic.com/firebasejs/12.3.0/firebase-auth.js')]);
 const auth=sdk.getAuth(app.initializeApp(configuration));await sdk.setPersistence(auth,sdk.inMemoryPersistence);
 authReady=true;signin.disabled=false;expired();signin.onclick=async()=>{if(signingIn||finishing||expired())return;
 const current=++attempt;signingIn=true;signin.disabled=true;approve.disabled=true;user=null;
 status.textContent='Opening Google sign-in. Complete it in the popup window.';
 try{const result=await sdk.signInWithPopup(auth,new sdk.GoogleAuthProvider());if(current!==attempt||expired())return;
 user=result.user;status.textContent='Signed in. Review the access below, then choose Allow.';approve.disabled=false;}
 catch(error){if(current!==attempt)return;const code=error?.code;
 status.textContent=code==='auth/popup-blocked'?'Google sign-in was blocked. Allow popups for this Fork page, then try again.':
 (code==='auth/popup-closed-by-user'||code==='auth/cancelled-popup-request')?'Google sign-in was canceled. You can try again.':
 code==='auth/network-request-failed'?'Google sign-in could not connect. Check your connection and try again.':
 'Google sign-in could not start. Try again, or return to ChatGPT and restart Connect.';}
 finally{if(current===attempt){signingIn=false;signin.disabled=false;expired();}}};
}catch{status.textContent='Sign-in is temporarily unavailable. Restart this connection later.';}
""".replace("FLOW", json.dumps(flow)).replace("EXPIRES", json.dumps(expires_at if expires_at is not None else self._now()+self.config.flow_ttl)).replace("FIREBASECONFIG", json.dumps(self.firebase_config).replace("</", "<\\/"))
        return ("<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Connect Fork Microscope to ChatGPT</title><style nonce='"+nonce+"'>"
            "body{font:17px system-ui;color:#172225;background:#f6f5ee;max-width:650px;margin:8vh auto;padding:24px;line-height:1.6}"
            "button{font:inherit;padding:12px 18px;margin:8px 8px 8px 0;border-radius:10px;border:1px solid #51605a;cursor:pointer}"
            "button:disabled{opacity:.5}h1{line-height:1.2}#approve{background:#173f35;color:white}</style>"
            "<main><h1>Connect Fork Microscope to ChatGPT</h1><p>Allow ChatGPT to access your single pilot account with <strong>fork:manage</strong>.</p>"
            "<p>ChatGPT can review plans, request approved compute, track or cancel investigations, and read or save your private evidence. "
            "A paid run still requires your approval of its exact plan. Connecting does not fund RunPod or start a GPU.</p>"
            "<p>Evidence is stored privately for 30 days, with a 1 GiB pilot quota. You can revoke this connection.</p>"
            "<p id='status' role='status'>Sign in with the approved Google account, then choose Allow.</p>"
            "<button id='signin' disabled>Sign in with Google</button><br><button id='approve' disabled>Allow ChatGPT access</button>"
            "<button id='deny'>Cancel</button><p><a href='https://chatgpt.com/settings/plugins-settings'>Return to ChatGPT to restart Connect</a></p></main><script type='module' nonce='"+nonce+"'>"+script+"</script></html>")

    def get_routes(self, mcp_path=None):
        if mcp_path not in (None, "/mcp"):
            raise ValueError("Pilot MCP must use the approved /mcp resource")
        routes = super().get_routes("/mcp")
        result = []
        async def metadata(request):
            c = self.config
            return self._secure_headers(JSONResponse({"issuer":c.issuer,
                "authorization_endpoint":c.issuer+"/authorize", "token_endpoint":c.issuer+"/token",
                "revocation_endpoint":c.issuer+"/revoke", "scopes_supported":[SCOPE],
                "response_types_supported":["code"], "grant_types_supported":["authorization_code","refresh_token"],
                "token_endpoint_auth_methods_supported":["none"], "revocation_endpoint_auth_methods_supported":["none"],
                "code_challenge_methods_supported":["S256"], "client_id_metadata_document_supported":True,
                "authorization_response_iss_parameter_supported":True}))
        async def resource_metadata(request):
            return self._secure_headers(JSONResponse({"resource":self.config.resource,
                "authorization_servers":[self.config.issuer], "scopes_supported":[SCOPE],
                "bearer_methods_supported":["header"]}))
        for route in routes:
            if route.path == "/authorize":
                result.append(Route("/authorize", self._authorize_route, methods=["GET", "POST"]))
            elif route.path == "/.well-known/oauth-authorization-server":
                result.append(Route(route.path, metadata, methods=["GET"]))
            elif route.path.startswith("/.well-known/oauth-protected-resource"):
                result.append(Route(route.path, resource_metadata, methods=["GET"]))
            elif route.path == "/token":
                result.append(Route(route.path, self._token_route, methods=["POST"]))
            elif route.path == "/revoke":
                result.append(Route(route.path, self._revoke_route, methods=["POST"]))
            else:
                result.append(route)
        result.append(Route("/oauth/consent", self._consent_route, methods=["GET", "POST"]))
        return result


class PilotOAuthCleanup:
    """Bounded hourly maintenance, called by the existing controller scheduler.

    The injected maintenance store supplies candidates(collection, cutoff, after,
    limit) and transaction(fn), with get/set/delete on its transaction. Production
    uses firestore_oauth_cleanup below and the existing Firestore client. Cursors
    allow progress past unmarked/other-owner rows without deleting or migrating
    them. A pass examines at most six times batch_per_collection candidates.
    """
    def __init__(self, store, *, pilot_uid, clock=time.time, batch_per_collection=20):
        if (getattr(store, "namespace", None) != "hosted_v1"
                or not callable(getattr(store, "candidates", None))
                or not callable(getattr(store, "transaction", None))
                or not isinstance(pilot_uid, str) or not OWNER.fullmatch(pilot_uid)
                or not callable(clock) or type(batch_per_collection) is not int
                or not 1 <= batch_per_collection <= 50):
            raise ValueError("Configure bounded cleanup for the exact pilot account and namespace")
        self.store, self.uid, self.clock, self.limit = store, pilot_uid, clock, batch_per_collection

    def __repr__(self):
        return "PilotOAuthCleanup(bounded=True, existing_firestore_identity=True)"

    def _owned(self, row):
        return isinstance(row, dict) and row.get("policy") == _POLICY and row.get("owner_uid") == self.uid

    @staticmethod
    def _timestamp(value):
        return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None

    def __call__(self):
        now = self.clock()
        if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
            raise ValueError("OAuth cleanup clock unavailable")
        cutoff = datetime.fromtimestamp(now, timezone.utc)
        removed = examined = 0
        try:
            for kind, collection in _COLLECTIONS.items():
                cursor_key = "cleanup_" + _sha(self.uid) + "_" + kind
                prior = self.store.transaction(lambda tx:tx.get(_COLLECTIONS["limits"], cursor_key))
                if prior is not None and not self._owned(prior):
                    raise ValueError()
                after = prior.get("cursor") if prior else None
                if after is not None and (not isinstance(after, dict) or set(after) != {"key", "delete_after"}
                        or not isinstance(after["key"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", after["key"])
                        or not self._timestamp(after["delete_after"])):
                    raise ValueError()
                candidates = self.store.candidates(collection, cutoff, after, self.limit)
                if not isinstance(candidates, list) or len(candidates) > self.limit:
                    raise ValueError()
                for candidate in candidates:
                    if (not isinstance(candidate, dict) or set(candidate) != {"key", "delete_after"}
                            or not isinstance(candidate["key"], str)
                            or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", candidate["key"])
                            or not self._timestamp(candidate["delete_after"])):
                        raise ValueError()
                    key = candidate["key"]
                    def remove(tx):
                        # Fresh transactional read: a renewed expiry, changed
                        # owner or removed policy marker defeats deletion.
                        row = tx.get(collection, key)
                        if (self._owned(row) and self._timestamp(row.get("delete_after"))
                                and row["delete_after"] <= cutoff):
                            tx.delete(collection, key)
                            return True
                        return False
                    removed += bool(self.store.transaction(remove))
                    examined += 1
                following = candidates[-1] if len(candidates) == self.limit else None
                if following != after:
                    def checkpoint(tx):
                        # Concurrent maintenance can repeat work safely; it must
                        # not overwrite a newer worker's progress or ownership.
                        if tx.get(_COLLECTIONS["limits"], cursor_key) != prior:
                            return
                        tx.set(_COLLECTIONS["limits"], cursor_key, dict(policy=_POLICY,
                            owner_uid=self.uid, cursor=following,
                            delete_after=datetime.fromtimestamp(now + _RETENTION_SECONDS, timezone.utc)))
                    self.store.transaction(checkpoint)
            return {"removed_oauth_rows":removed, "examined_oauth_rows":examined}
        except Exception:
            # Partial deletion is safe and the next hourly pass is idempotent.
            raise ValueError("OAuth cleanup temporarily unavailable") from None


class _FirestoreOAuthCleanupStore:
    """Narrow delete adapter; it never creates a client or obtains credentials."""
    def __init__(self, store, sdk):
        if getattr(store, "namespace", None) != "hosted_v1" or getattr(store, "client", None) is None:
            raise ValueError("Existing hosted_v1 Firestore store is required")
        self.namespace, self.client, self.sdk = "hosted_v1", store.client, sdk

    def _collection(self, name):
        if name not in _COLLECTIONS.values():
            raise ValueError("OAuth cleanup collection is not permitted")
        return self.client.collection(self.namespace + "_" + name)

    def candidates(self, collection, cutoff, after, limit):
        ref = self._collection(collection)
        # One existing single-field index; no composite query or index change.
        query = ref.where(filter=self.sdk.FieldFilter("delete_after", "<=", cutoff))
        query = query.order_by("delete_after").order_by("__name__").select(["delete_after"])
        if after is not None:
            query = query.start_after({"delete_after":after["delete_after"], "__name__":ref.document(after["key"])})
        return [{"key":snapshot.id, "delete_after":snapshot.to_dict()["delete_after"]}
                for snapshot in query.limit(limit).stream()]

    def transaction(self, fn):
        adapter = self
        class Transaction:
            def __init__(self, transaction):
                self.transaction = transaction
            def get(self, collection, key):
                snap = adapter._collection(collection).document(key).get(transaction=self.transaction)
                return snap.to_dict() if snap.exists else None
            def set(self, collection, key, value):
                self.transaction.set(adapter._collection(collection).document(key), value)
            def delete(self, collection, key):
                self.transaction.delete(adapter._collection(collection).document(key))
        transaction = self.client.transaction()
        @self.sdk.transactional
        def execute(transaction):
            return fn(Transaction(transaction))
        return execute(transaction)


def firestore_oauth_cleanup(store, *, pilot_uid, clock=time.time, batch_per_collection=20):
    """Build the callable for the controller's existing hourly maintenance path.

    Uses only the supplied client/identity and hosted_v1_mcp_oauth_* collections.
    No scans, deletes, network calls, TTL settings or index changes occur here.
    """
    from google.cloud import firestore
    return PilotOAuthCleanup(_FirestoreOAuthCleanupStore(store, firestore), pilot_uid=pilot_uid,
        clock=clock, batch_per_collection=batch_per_collection)
