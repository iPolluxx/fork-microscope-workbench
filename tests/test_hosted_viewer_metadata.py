"""Real FastMCP resource parity and mounted OAuth HTTP tests; fake-only accounts."""
import asyncio
import hashlib
import re
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastmcp import Client
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

from tools import viewer
from tools.hosted import create_hosted_mcp
from pilot_cloud import mount_managed_mcp
from pilot_budget import PilotBudget, BudgetedService
from fork_microscope.hosted.service import HostedService
from test_pilot_oauth import Fixture, ISSUER, UID, grant


def forbidden(*args, **kwargs):
    raise AssertionError('No evidence, cloud, provider or model operation is allowed')


class Library:
    max_artifact = 0
    import_bytes = forbidden
    profile_for = forbidden
    list_investigations = forbidden


def assert_resource(contents):
    assert contents['uri'] == viewer.RESOURCE_URI
    assert contents['mimeType'] == 'text/html;profile=mcp-app'
    assert contents['text'] == viewer.VIEWER_HTML
    assert '<script type="module">' in contents['text']
    assert 'ui/initialize' in contents['text']
    assert not re.search(r'<(?:script|link|iframe)[^>]+(?:src|href)=', contents['text'])
    assert contents['_meta']['ui'] == {
        'prefersBorder': True,
        'csp': {'connectDomains': [], 'resourceDomains': [], 'frameDomains': []},
    }
    assert contents['_meta']['openai/ui']['availableDisplayModes'] == ['inline', 'fullscreen']
    assert contents['_meta']['openai/widgetDescription']


def test_hosted_viewer_resource_metadata_matches_synthetic():
    auth = StaticTokenVerifier(tokens={})
    facade = SimpleNamespace(library=Library(), service=SimpleNamespace(clock=lambda: 100))
    hosted = create_hosted_mcp(facade, auth=auth, owner_resolver=lambda token: token.subject)

    async def check():
        async with Client(viewer.viewer_mcp) as synthetic, Client(hosted) as pilot:
            left = (await synthetic.read_resource(viewer.RESOURCE_URI))[0]
            right = (await pilot.read_resource(viewer.RESOURCE_URI))[0]
            assert right.meta == left.meta
            assert right.text == left.text and right.mime_type == left.mime_type
            assert_resource({'uri': str(right.uri), 'mimeType': right.mime_type,
                             'text': right.text, '_meta': right.meta})
    asyncio.run(asyncio.wait_for(check(), timeout=20))


@pytest.mark.parametrize('version', ['2025-06-18', '2025-11-25'])
def test_mounted_viewer_http_preserves_metadata_and_rejects_unauthorized(version):
    fixture = Fixture()
    budget = PilotBudget(fixture.store, pilot_uid=UID, clock=lambda: fixture.now)
    app = FastAPI()
    app.state.service = BudgetedService(HostedService(fixture.store, clock=lambda: fixture.now), budget)
    mount_managed_mcp(app, provider=fixture.provider, library=Library(), pilot_uid=UID,
                     budget=budget, issuer=ISSUER, api_host='pilot.example.test')

    async def check():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ISSUER) as client:
                headers = {'Accept': 'application/json, text/event-stream',
                           'MCP-Protocol-Version': version}

                async def rpc(method, params):
                    return await client.post('/mcp', headers=headers,
                        json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})

                unauth = await rpc('resources/read', {'uri': viewer.RESOURCE_URI})
                assert unauth.status_code == 401
                assert ISSUER + '/.well-known/oauth-protected-resource/mcp' in unauth.headers['www-authenticate']
                tokens = await grant(client)  # Existing fake-only OAuth fixture; no real credentials.
                headers['Authorization'] = 'Bearer ' + tokens['access_token']

                tools = (await rpc('tools/list', {})).json()['result']['tools']
                render = next(t for t in tools if t['name'] == 'fork_microscope_render_investigation')
                assert render['_meta']['ui']['resourceUri'] == viewer.RESOURCE_URI
                assert 'app' in render['_meta']['ui']['visibility']
                assert render['_meta']['securitySchemes'] == [{'type': 'oauth2', 'scopes': ['fork:manage']}]
                assert 'fork_microscope_import_investigation' in {t['name'] for t in tools}

                listed = (await rpc('resources/list', {})).json()['result']['resources']
                resource = next(r for r in listed if r['uri'] == viewer.RESOURCE_URI)
                response = await rpc('resources/read', {'uri': resource['uri']})
                assert response.status_code == 200
                assert response.headers['content-type'].startswith('application/json')
                contents = response.json()['result']['contents'][0]
                assert_resource(contents)
                for key, value in contents['_meta'].items():
                    assert resource['_meta'][key] == value
                assert hashlib.sha256(contents['text'].encode()).hexdigest()[:16] in resource['uri']

                missing = await rpc('resources/read', {'uri': 'ui://fork-microscope/unknown.html'})
                assert missing.status_code == 200 and missing.json()['error']['code'] == -32602
                assert 'result' not in missing.json()
                headers['Authorization'] = 'Bearer invalid-offline-fixture'
                assert (await rpc('resources/read', {'uri': viewer.RESOURCE_URI})).status_code == 401
                headers['Authorization'] = 'Bearer ' + tokens['access_token']
                fixture.allowed = False
                assert (await rpc('resources/read', {'uri': viewer.RESOURCE_URI})).status_code == 401
    asyncio.run(asyncio.wait_for(check(), timeout=20))
