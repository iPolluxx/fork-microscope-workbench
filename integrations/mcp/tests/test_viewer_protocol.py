"""Actual MCP discovery/resource/render/data flow with saved evidence, no worker."""
import asyncio
import hashlib
import json
from pathlib import Path
import re

from test_stdio_protocol import client, protocol_profiles, archive

ROOT = Path(__file__).resolve().parents[3]


def test_viewer_resource_and_saved_graph_over_stdio(protocol_profiles):
    profiles, _ = protocol_profiles
    raw = (ROOT / 'public/fork-microscope/demo-portfolio.json').read_bytes()
    (profiles[0].parent / 'evidence/qwen.json').write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()

    async def check():
        async with client(profiles[0]) as c:
            tools = {t.name:t for t in await c.list_tools()}
            render = tools['fork_microscope_render_investigation']
            resource_uri = render.meta['ui']['resourceUri']
            assert resource_uri.startswith('ui://fork-microscope/saved-evidence-')
            assert render.meta['ui']['visibility'] == ['model']
            assert 'resourceUri' not in tools['fork_microscope_get_investigation_view'].meta['ui']
            allowed = {'fork_microscope_get_investigation_view','fork_microscope_inspect_investigation','fork_microscope_read_investigation_bytes'}
            assert {name for name,tool in tools.items() if 'app' in tool.meta['ui']['visibility']} == allowed
            listed = await c.list_resources()
            assert resource_uri in {str(r.uri) for r in listed}
            content = (await c.read_resource(resource_uri))[0]
            assert content.mime_type == 'text/html;profile=mcp-app'
            assert content.meta['ui']['csp'] == {'connectDomains':[],'resourceDomains':[],'frameDomains':[]}
            html = content.text
            assert '<script type="module">' in html and 'ui/initialize' in html
            assert not re.search(r'<(?:script|link|iframe)[^>]+(?:src|href)=',html)
            assert sha not in html and 'Qwen/Qwen2.5' not in html
            result = await c.call_tool('fork_microscope_render_investigation',{'bundle_id':'qwen.json','expected_file_sha256':sha})
            view=result.data
            assert view['schema']=='fork-microscope-view-v1'
            assert view['selection']['token_position']==16
            assert view['bundle']['file_sha256']==sha
            assert view['selected']['continuations']['total']==20
            assert len(view['selected']['continuations']['items'])==5
            assert view['selected']['continuations']['next_offset']==5
            page = (await c.call_tool('fork_microscope_get_investigation_view',{'bundle_id':'qwen.json','expected_file_sha256':sha,'token_position':80,'offset':15})).data
            assert page['selected']['contrast']==[]
            assert page['selected']['continuations']['next_offset'] is None
            for name in ['fork_microscope_render_investigation','fork_microscope_get_investigation_view']:
                rejected=await c.call_tool(name,{'bundle_id':'qwen.json','expected_file_sha256':'0'*64},raise_on_error=False)
                assert rejected.is_error
            # Private profile still has no model worker.
            settings=(await c.call_tool('fork_microscope_workflow_settings',{})).data
            assert settings['worker_configured'] is False
    asyncio.run(asyncio.wait_for(check(),timeout=75))
