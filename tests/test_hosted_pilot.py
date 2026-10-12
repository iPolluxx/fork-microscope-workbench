"""No listener, remote auth, account, credentials or provider calls in this suite."""
from dataclasses import replace
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastmcp.server.auth import (AccessToken, OAuthProvider, RemoteAuthProvider,
                                StaticTokenVerifier, TokenVerifier)
from pydantic import AnyHttpUrl

import hosted_pilot as pilot


ISSUER = "https://issuer.example.test/"
RESOURCE = "https://mcp.example.test/mcp"
SUBJECT = "provider|opaque-user"
UID = "hosted-account-1"
SECRET = "fake-sensitive-token-never-display"


def link(**changes):
    return pilot.PilotAccountLink(**dict(dict(issuer=ISSUER, subject=SUBJECT, hosted_uid=UID, resource=RESOURCE), **changes))


def token(**changes):
    values = dict(token=SECRET, client_id="chatgpt-client", scopes=["fork:manage"],
        expires_at=2000, subject=SUBJECT, resource=RESOURCE,
        claims={"iss": ISSUER, "sub": SUBJECT, "aud": RESOURCE, "exp": 2000})
    values.update(changes)
    return AccessToken(**values)


class FixtureVerifier(TokenVerifier):
    """Injected fixture only; verify_token must never be called by assembly."""
    async def verify_token(self, value):
        raise AssertionError("No verification/network call during assembly")


def auth(**changes):
    values = dict(token_verifier=FixtureVerifier(required_scopes=["fork:manage"]),
        authorization_servers=[AnyHttpUrl(ISSUER)], base_url="https://mcp.example.test")
    values.update(changes)
    return RemoteAuthProvider(**values)


def test_exact_verified_identity_and_per_call_access_check():
    check = Mock(return_value=True)
    owner = pilot.create_owner_resolver(link(), access_check=check, clock=lambda: 1000)
    assert owner(token()) == UID
    verified = token(claims={"iss":ISSUER,"sub":SUBJECT,"aud":[RESOURCE],"exp":2000})
    assert owner(verified) == UID
    assert check.call_count == 2
    check.assert_called_with(issuer=ISSUER, subject=SUBJECT, hosted_uid=UID, token=verified)


@pytest.mark.parametrize("changes", [
    {"iss": "https://another.example.test/"}, {"iss": ISSUER.rstrip("/")},
    {"sub": "another-user"}, {"aud": "https://another.example.test/mcp"},
    {"aud": [RESOURCE, "https://another.example.test/mcp"]},
    {"exp": 1000}, {"exp": None}, {"exp": True}, {"exp": "2000"},
    {"exp": float("nan")}, {"exp": float("inf")}, {"exp": 3000},
    {"nbf": 1001}, {"nbf": "1"},
])
def test_wrong_claims_fail_before_canonical_access(changes):
    check = Mock(return_value=True)
    owner = pilot.create_owner_resolver(link(), access_check=check, clock=lambda: 1000)
    claims = dict(token().claims, **changes)
    with pytest.raises(pilot.PilotAuthorizationError) as error:
        owner(token(claims=claims))
    check.assert_not_called()
    assert SECRET not in str(error.value) and SUBJECT not in repr(error.value)


@pytest.mark.parametrize("missing", ["iss", "sub", "aud", "exp"])
def test_required_claims_have_no_fallback(missing):
    claims = token().claims
    claims.pop(missing)
    owner = pilot.create_owner_resolver(link(), access_check=lambda **kw: True, clock=lambda: 1000)
    with pytest.raises(pilot.PilotAuthorizationError):
        owner(token(claims=claims, client_id=SUBJECT))


@pytest.mark.parametrize("value", [SECRET, {}, SimpleNamespace(subject=SUBJECT), None])
def test_only_fastmcp_access_token_context_is_accepted(value):
    owner = pilot.create_owner_resolver(link(), access_check=lambda **kw: True, clock=lambda: 1000)
    with pytest.raises(pilot.PilotAuthorizationError):
        owner(value)


def test_subject_never_comes_from_email_or_client_id():
    owner = pilot.create_owner_resolver(link(), access_check=lambda **kw: True, clock=lambda: 1000)
    for subject in (None, "someone-else"):
        with pytest.raises(pilot.PilotAuthorizationError):
            owner(token(subject=subject, client_id=SUBJECT,
                claims={"iss":ISSUER,"sub":subject,"aud":RESOURCE,"exp":2000,"email":SUBJECT}))


@pytest.mark.parametrize("changes", [{"expires_at":None}, {"expires_at":999},
    {"resource":"https://another.example.test/mcp"}, {"scopes":["other:read"]}, {"subject":None}])
def test_access_token_fields_are_defensively_checked(changes):
    owner = pilot.create_owner_resolver(link(), access_check=lambda **kw: True, clock=lambda: 1000)
    with pytest.raises(pilot.PilotAuthorizationError):
        owner(token(**changes))


def test_revocation_and_access_errors_fail_closed_after_prior_success():
    state = [True]
    def check(**kwargs):
        if isinstance(state[0], Exception):
            raise state[0]
        return state[0]
    owner = pilot.create_owner_resolver(link(), access_check=check, clock=lambda: 1000)
    assert owner(token()) == UID
    for value in (False, None, 1, "yes", RuntimeError(SECRET)):
        state[0] = value
        with pytest.raises(pilot.PilotAuthorizationError) as error:
            owner(token())
        assert SECRET not in str(error.value)


def test_authoritative_callback_can_revoke_one_grant_without_disabling_same_owner():
    revoked = {"grant-revoked"}
    seen = []
    def check(*, issuer, subject, hosted_uid, token):
        assert (issuer, subject, hosted_uid) == (ISSUER, SUBJECT, UID)
        seen.append(token)
        return token.claims.get("grant_id") not in revoked
    owner = pilot.create_owner_resolver(link(), access_check=check, clock=lambda:1000)
    current = token(claims=dict(token().claims, grant_id="grant-active", iat=900))
    old = token(claims=dict(token().claims, grant_id="grant-revoked", iat=850))
    assert owner(current) == UID
    with pytest.raises(pilot.PilotAuthorizationError):
        owner(old)
    assert owner(current) == UID
    assert seen == [current, old, current]


def test_async_or_absent_authoritative_callback_is_not_accepted():
    async def async_check(**kw):
        return True
    for check in (None, async_check):
        with pytest.raises(ValueError):
            pilot.create_owner_resolver(link(), access_check=check)


@pytest.mark.parametrize("url", ["http://issuer.example.test", "https://key@issuer.example.test",
    "https://issuer.example.test?secret=value", "https://issuer.example.test/#fragment",
    "https://issuer.example.test:bad", "https://issuer.example.test:8443",
    "https://issuer.example.test/a/../b", "https://issuer.example.test/%2fsecret", "https://issuer.example.test\n"])
def test_link_rejects_noncanonical_https_urls_without_echo(url):
    with pytest.raises(ValueError) as error:
        link(issuer=url)
    assert url not in str(error.value)


def test_link_resource_subject_uid_and_repr_are_bounded():
    for changes in ({"subject":""}, {"subject":"user\n"}, {"subject":"x"*256},
                    {"hosted_uid":"../owner"}, {"resource":"https://mcp.example.test/other"}):
        with pytest.raises(ValueError):
            link(**changes)
    assert SUBJECT not in repr(link()) and UID not in repr(link()) and ISSUER not in repr(link())


def assembled(monkeypatch, tmp_path, **changes):
    provider = auth()
    mcp = SimpleNamespace(auth=provider, run=Mock())
    assembler = Mock(return_value=mcp)
    monkeypatch.setattr(pilot, "assemble_hosted_mcp", assembler)
    args = dict(public_app=SimpleNamespace(state=SimpleNamespace(service=SimpleNamespace(public=lambda:None),
            controller_client=object())), auth=provider, link=link(), access_check=lambda **kw:True,
        setup_url="https://setup.example.test/hosted", durable_evidence_root=tmp_path,
        durable_storage_confirmed=True, clock=lambda:1000)
    args.update(changes)
    mcp.auth = args["auth"]
    value = pilot.assemble_pilot_mcp(**args)
    return value, assembler


def test_assembly_wires_only_configured_identity_and_durable_confirmation(monkeypatch, tmp_path):
    value, assembler = assembled(monkeypatch, tmp_path)
    assert assembler.call_count == 1 and value.mcp.run.call_count == 0
    assert assembler.call_args.kwargs["durable_storage_confirmed"] is True
    assert assembler.call_args.kwargs["owner_resolver"](token()) == UID
    assert SUBJECT not in repr(value) and SECRET not in repr(value)


def test_external_https_resource_can_have_exact_proxy_prefix(monkeypatch, tmp_path):
    resource = "https://mcp.example.test/managed/fork/mcp"
    value, assembler = assembled(monkeypatch, tmp_path,
        auth=auth(base_url="https://mcp.example.test/managed/fork"), link=link(resource=resource))
    assert assembler.call_args.kwargs["owner_resolver"](token(resource=resource,
        claims={"iss":ISSUER,"sub":SUBJECT,"aud":resource,"exp":2000})) == UID
    pilot.run_loopback_pilot(value)
    assert value.mcp.run.call_args.kwargs["path"] == "/mcp"


@pytest.mark.parametrize("changes", [{"durable_storage_confirmed":False}, {"durable_storage_confirmed":1},
    {"auth":None}, {"link":None}, {"access_check":None}, {"public_app":None}])
def test_missing_deployment_dependencies_reject_before_assembly(monkeypatch, tmp_path, changes):
    with pytest.raises(ValueError):
        assembled(monkeypatch, tmp_path, **changes)
    pilot.assemble_hosted_mcp.assert_not_called()


def test_static_verifier_bare_and_wrapped_are_rejected(monkeypatch, tmp_path):
    static = StaticTokenVerifier(tokens={"fixture":{"client_id":"fixture","scopes":["fork:manage"]}},
        required_scopes=["fork:manage"])
    for provider in (static, auth(token_verifier=static)):
        with pytest.raises(ValueError, match="OAuth"):
            assembled(monkeypatch, tmp_path, auth=provider)
        pilot.assemble_hosted_mcp.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"base_url":"https://other.example.test"},
    {"authorization_servers":[AnyHttpUrl("https://other.example.test/")]},
    {"token_verifier":FixtureVerifier(required_scopes=["other:read"])}])
def test_auth_discovery_and_scope_must_match_pilot(monkeypatch, tmp_path, changes):
    with pytest.raises(ValueError, match="OAuth"):
        assembled(monkeypatch, tmp_path, auth=auth(**changes))


def test_unimplemented_oauth_provider_is_not_treated_as_real(monkeypatch, tmp_path):
    provider = OAuthProvider(base_url="https://mcp.example.test", issuer_url=ISSUER, required_scopes=["fork:manage"])
    with pytest.raises(ValueError, match="OAuth"):
        assembled(monkeypatch, tmp_path, auth=provider)


def test_unimplemented_remote_verifier_is_not_treated_as_real(monkeypatch, tmp_path):
    provider = auth(token_verifier=TokenVerifier(required_scopes=["fork:manage"]))
    with pytest.raises(ValueError, match="OAuth"):
        assembled(monkeypatch, tmp_path, auth=provider)


def test_real_assembly_registers_managed_tools_without_listener_or_provider_calls(tmp_path, monkeypatch):
    from fastmcp import Client
    import fastmcp.server.dependencies as dependencies
    from fork_microscope.hosted.service import HostedService
    from fork_microscope.hosted.store import MemoryStore
    service = HostedService(MemoryStore(), clock=lambda:1000)
    app = SimpleNamespace(state=SimpleNamespace(service=service, controller_client=object()))
    check = Mock(return_value=True)
    # Fixture for the verified token returned by request authentication.
    monkeypatch.setattr(dependencies, "get_access_token", lambda:token())
    value = pilot.assemble_pilot_mcp(app, auth=auth(), link=link(), access_check=check,
        setup_url="https://setup.example.test/hosted", durable_evidence_root=tmp_path/"private-evidence",
        durable_storage_confirmed=True, clock=lambda:1000)
    assert value.mcp.auth is value._auth
    # In-process fixture context represents the AccessToken returned by auth.
    # No HTTP listener, discovery, signature verification or provider call occurs.
    async def inspect_tools():
        async with Client(value.mcp) as client:
            names = {tool.name for tool in await client.list_tools()}
            assert "fork_microscope_prepare_plan" in names
            assert "fork_microscope_start_workflow" not in names
            out = await client.call_tool("fork_microscope_compute_setup", {}, raise_on_error=False)
            assert not out.is_error
            assert check.call_count == 1
            check.return_value = False
            denied = await client.call_tool("fork_microscope_compute_setup", {}, raise_on_error=False)
            assert denied.is_error and check.call_count == 2
    asyncio.run(inspect_tools())


def test_launcher_uses_only_loopback_http_mcp_arguments(monkeypatch, tmp_path):
    value, _ = assembled(monkeypatch, tmp_path)
    pilot.run_loopback_pilot(value, port=8799)
    value.mcp.run.assert_called_once_with(transport="http", host="127.0.0.1", port=8799,
        path="/mcp", show_banner=False, log_level="warning", uvicorn_config={"access_log":False})


def test_launcher_rejects_unguarded_or_changed_auth_and_invalid_ports(monkeypatch, tmp_path):
    value, _ = assembled(monkeypatch, tmp_path)
    for invalid in (value.mcp, replace(value, _guard=None)):
        with pytest.raises(ValueError):
            pilot.run_loopback_pilot(invalid)
    for port in (True, 0, 80, 65536, "8788"):
        with pytest.raises(ValueError):
            pilot.run_loopback_pilot(value, port=port)
    value.mcp.auth = None
    with pytest.raises(ValueError):
        pilot.run_loopback_pilot(value)
    value.mcp.run.assert_not_called()
