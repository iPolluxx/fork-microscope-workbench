"""Explicit one-account managed MCP assembly; no OAuth provisioning or auto-start.

An operator must supply a configured verifying OAuth provider, the canonical link
used by secure hosted setup, and a synchronous authoritative access/revocation
check. These are deployment dependencies, never tool arguments. No environment,
local profile, email, client ID or unverified JWT fallback is consulted here.

Import and assembly open no listener and do not fetch discovery/JWKS. Calling the
launcher explicitly starts a loopback-only HTTP listener; exposing it through an
HTTPS proxy and configuring OAuth registration remain separate deployment gates.
"""
from dataclasses import dataclass, field
import inspect
import math
import re
import time
from urllib.parse import urlsplit

from fastmcp.server.auth import (AccessToken, AuthProvider, DebugTokenVerifier, JWTVerifier,
    OAuthProvider, RemoteAuthProvider, StaticTokenVerifier, TokenVerifier)

from fork_microscope.hosted.credentials import OWNER
from fork_microscope_hosted_mcp import assemble_hosted_mcp


_SCOPE = "fork:manage"
_ASSEMBLED = object()


class PilotAuthorizationError(ValueError):
    """Deliberately excludes token claims, credentials and identity values."""
    def __init__(self):
        super().__init__("Authenticated pilot account access could not be verified")


def _https_url(value, *, resource=False):
    try:
        if not isinstance(value, str) or not 1 <= len(value) <= 2048 or not value.isascii():
            raise ValueError()
        if any(ord(c) <= 32 or ord(c) >= 127 for c in value) or "\\" in value or "%" in value:
            raise ValueError()
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.port not in (None, 443)
                or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", parsed.hostname)
                or any(not part or len(part) > 63 or part.startswith("-") or part.endswith("-")
                       for part in parsed.hostname.split("."))
                or "//" in parsed.path or any(p in (".", "..") for p in parsed.path.split("/"))
                or (resource and not parsed.path.endswith("/mcp"))):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("Configure canonical HTTPS issuer and resource URLs ending in /mcp") from None
    return value


@dataclass(frozen=True, repr=False)
class PilotAccountLink:
    """One exact issuer/subject link to the existing hosted setup account."""
    issuer: str
    subject: str
    hosted_uid: str
    resource: str

    def __post_init__(self):
        _https_url(self.issuer)
        _https_url(self.resource, resource=True)
        if (not isinstance(self.subject, str) or not 1 <= len(self.subject) <= 255
                or not self.subject.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in self.subject)
                or not isinstance(self.hosted_uid, str) or not OWNER.fullmatch(self.hosted_uid)):
            raise ValueError("Configure the exact OAuth subject and canonical hosted account")

    def __repr__(self):
        return "PilotAccountLink(configured=True)"


def create_owner_resolver(link, *, access_check, clock=time.time):
    """Consume only the verified AccessToken from FastMCP request context.

    This function does not decode or verify raw bearer tokens. The auth provider
    verifies them before FastMCP calls the managed tool's owner resolver. Defense
    in depth checks claims again and then calls ``access_check(issuer=..., subject=
    ..., hosted_uid=..., token=verified_token)`` on every invocation. It must return
    literal True only while the token/grant and canonical account link are active
    and not revoked, including any account valid-since requirement; errors deny
    access. The trusted callback receives the verified token/claims for that check
    and must never log, persist or return its bearer value.
    """
    if (not isinstance(link, PilotAccountLink) or not callable(access_check)
            or inspect.iscoroutinefunction(access_check) or not callable(clock)):
        raise ValueError("Canonical account link and synchronous access check are required")

    def owner(verified_token):
        try:
            if not isinstance(verified_token, AccessToken):
                raise PilotAuthorizationError()
            claims = verified_token.claims
            if not isinstance(claims, dict):
                raise PilotAuthorizationError()
            audience = claims.get("aud")
            if (claims.get("iss") != link.issuer or claims.get("sub") != link.subject
                    or verified_token.subject != link.subject
                    or not (audience == link.resource or audience == [link.resource])
                    or (verified_token.resource is not None and verified_token.resource != link.resource)
                    or _SCOPE not in verified_token.scopes):
                raise PilotAuthorizationError()
            expiry, token_expiry, now = claims.get("exp"), verified_token.expires_at, clock()
            if (type(expiry) not in (int, float) or not math.isfinite(expiry)
                    or type(token_expiry) is not int or expiry != token_expiry
                    or type(now) not in (int, float) or not math.isfinite(now)
                    or expiry <= now):
                raise PilotAuthorizationError()
            # A future nbf must not be ignored even by a permissive verifier.
            not_before = claims.get("nbf")
            if not_before is not None and (type(not_before) not in (int, float)
                    or not math.isfinite(not_before) or not_before > now):
                raise PilotAuthorizationError()
            allowed = access_check(issuer=link.issuer, subject=link.subject,
                                   hosted_uid=link.hosted_uid, token=verified_token)
            if inspect.iscoroutine(allowed):
                allowed.close()
            if allowed is not True:
                raise PilotAuthorizationError()
            return link.hosted_uid
        except Exception:
            raise PilotAuthorizationError() from None
    return owner


def _metadata_url(value):
    # AnyHttpUrl adds a root slash. This is metadata-only; token issuer comparison
    # above deliberately preserves exact issuer spelling, including trailing slash.
    value = str(value)
    _https_url(value)
    return value + "/" if not urlsplit(value).path else value


def _validate_auth(auth, link):
    """Reject known development verifiers and incomplete/mismatched discovery.

    Type/configuration checks cannot certify an operator-written verifier's
    cryptography. Its verification and revocation behavior still needs deployment
    review; a callback and an AccessToken object are not substitutes for OAuth.
    """
    try:
        if not isinstance(auth, (RemoteAuthProvider, OAuthProvider)) or _SCOPE not in auth.required_scopes:
            raise ValueError()
        base = auth.resource_base_url or auth.base_url
        if base is None or str(base).rstrip("/") + "/mcp" != link.resource:
            raise ValueError()
        _https_url(str(base))
        if isinstance(auth, RemoteAuthProvider):
            verifier = auth.token_verifier
            if (isinstance(verifier, (StaticTokenVerifier, DebugTokenVerifier))
                    or not callable(getattr(verifier, "verify_token", None))
                    or getattr(type(verifier), "verify_token", None) in (AuthProvider.verify_token, TokenVerifier.verify_token)
                    or [_metadata_url(v) for v in auth.authorization_servers] != [_metadata_url(link.issuer)]):
                raise ValueError()
            if isinstance(verifier, JWTVerifier) and (verifier.issuer not in (link.issuer, [link.issuer])
                    or verifier.audience not in (link.resource, [link.resource])):
                raise ValueError()
        else:
            if (_metadata_url(auth.issuer_url) != _metadata_url(link.issuer)
                    or (type(auth).load_access_token is OAuthProvider.load_access_token
                        and type(auth).verify_token is OAuthProvider.verify_token)):
                raise ValueError()
    except Exception:
        raise ValueError("A configured verifying OAuth provider for this issuer, resource and scope is required") from None


@dataclass(frozen=True, repr=False)
class HostedPilot:
    """Result of guarded assembly; repr never includes providers or account data."""
    mcp: object = field(repr=False)
    _auth: object = field(repr=False)
    _link: PilotAccountLink = field(repr=False)
    _guard: object = field(repr=False, default=None)

    def __repr__(self):
        return "HostedPilot(guarded=True)"


def assemble_pilot_mcp(public_app, *, auth, link, access_check, setup_url,
                       durable_evidence_root, durable_storage_confirmed=False, clock=time.time):
    """Assemble the existing managed tools without creating OAuth credentials.

    ``durable_storage_confirmed`` is an operator assertion after verifying the
    evidence volume. The same ``link.hosted_uid`` must be used by secure hosted
    setup. Assembly alone does not establish that deployment prerequisite.
    """
    if durable_storage_confirmed is not True:
        raise ValueError("Configure and verify durable private evidence storage before pilot assembly")
    if not isinstance(link, PilotAccountLink):
        raise ValueError("Canonical pilot account link is required")
    _validate_auth(auth, link)
    resolver = create_owner_resolver(link, access_check=access_check, clock=clock)
    try:
        state = public_app.state
        if not callable(state.service.public) or state.controller_client is None:
            raise ValueError()
    except Exception:
        raise ValueError("Configured hosted service and controller client are required") from None
    mcp = assemble_hosted_mcp(public_app, auth=auth, owner_resolver=resolver,
        setup_url=setup_url, durable_evidence_root=durable_evidence_root,
        durable_storage_confirmed=True)
    return HostedPilot(mcp=mcp, _auth=auth, _link=link, _guard=_ASSEMBLED)


def run_loopback_pilot(pilot, *, port=8788):
    """Explicit start only; bind 127.0.0.1 HTTP /mcp with no override escape hatch.

    No public listener, tunnel, proxy, OAuth registration or persistent service is
    created here. A separate approved HTTPS deployment is required for ChatGPT.
    """
    if (not isinstance(pilot, HostedPilot) or pilot._guard is not _ASSEMBLED
            or getattr(pilot.mcp, "auth", None) is not pilot._auth):
        raise ValueError("Use a guarded pilot assembly with its original OAuth provider")
    _validate_auth(pilot._auth, pilot._link)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Pilot port must be an unprivileged TCP port")
    pilot.mcp.run(transport="http", host="127.0.0.1", port=port, path="/mcp",
        show_banner=False, log_level="warning", uvicorn_config={"access_log": False})
