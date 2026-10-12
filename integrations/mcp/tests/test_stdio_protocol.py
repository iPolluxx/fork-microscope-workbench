"""Real MCP JSON-RPC over child-process stdio; synthetic evidence, no worker.

Run with FastMCP installed and the repository's dependencies on PYTHONPATH.
The public Client/PythonStdioTransport APIs own and stop every child process.
"""
import asyncio
import base64
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tests'))
from test_evidence_io import archive
from test_workflow import config
from fork_microscope.investigation_bundle import build_bundle


@pytest.fixture
def protocol_profiles(tmp_path, archive):
    pytest.importorskip('fastmcp', reason='Real stdio tests require FastMCP')
    profiles = []
    for name in ('first', 'second'):
        profile = tmp_path / name / 'profile.json'
        subprocess.run([sys.executable, str(ROOT / 'integrations/mcp/src/configure.py'),
                        'init', '--profile', str(profile)], check=True, capture_output=True)
        profiles.append(profile)
    raw = json.dumps(build_bundle([archive]), indent=2).encode()
    evidence = profiles[0].parent / 'evidence'
    (evidence / 'saved.json').write_bytes(raw)
    (evidence / 'cross-profile.json').symlink_to(profiles[1].parent / 'evidence/private.json')
    (profiles[1].parent / 'evidence/private.json').write_bytes(raw)
    # Local ownership metadata only: never configure a URL/token or invoke inference.
    p = json.loads(profiles[0].read_text())
    ledger = dict(profile_id=p['profile_id'], jobs={'b' * 32: {'worker_url': None}})
    ledger_path = profiles[0].parent / 'state/ledger.json'
    ledger_path.write_text(json.dumps(ledger)); ledger_path.chmod(0o600)
    return profiles, raw


def client(profile, mode='auto'):
    from fastmcp import Client
    from fastmcp.client.transports import PythonStdioTransport
    paths = [str(ROOT / 'src'), str(ROOT / 'integrations/mcp/src')]
    if os.environ.get('PYTHONPATH'):
        paths.append(os.environ['PYTHONPATH'])
    transport = PythonStdioTransport(
        ROOT / 'integrations/mcp/src/fork_microscope_workbench_mcp.py',
        python_cmd=sys.executable, cwd=str(ROOT), keep_alive=False,
        env={'FORK_MICROSCOPE_PROFILE': str(profile), 'PYTHONPATH': os.pathsep.join(paths),
             'FASTMCP_CHECK_FOR_UPDATES': 'off'})
    return Client(transport, timeout=20, init_timeout=20, mode=mode)


async def success(c, name, **arguments):
    result = await c.call_tool('fork_microscope_' + name, arguments)
    assert not result.is_error
    assert isinstance(result.data, dict)
    return result.data


async def failure(c, name, match, **arguments):
    result = await c.call_tool('fork_microscope_' + name, arguments, raise_on_error=False)
    assert result.is_error, (name, arguments, result)
    text = ' '.join(getattr(item, 'text', '') for item in result.content)
    assert match.lower() in text.lower(), text


@pytest.mark.parametrize("protocol_mode", ["auto", "legacy"])
def test_stdio_discovery_saved_evidence_roundtrip(protocol_profiles, protocol_mode, record_property):
    profiles, raw = protocol_profiles
    sha = hashlib.sha256(raw).hexdigest()
    bundle = json.loads(raw)

    async def check():
        async with client(profiles[0], mode=protocol_mode) as c:
            record_property("protocol_version", c.protocol_version)
            assert c.protocol_version
            discovered = await c.list_tools()
            names = {tool.name for tool in discovered}
            assert names == {'fork_microscope_' + name for name in (
                'list_investigations', 'inspect_investigation', 'export_investigation',
                'report_investigation', 'read_investigation_bytes', 'workflow_settings',
                'start_workflow', 'workflow_status', 'cancel_workflow',
                'resume_workflow', 'export_workflow', 'render_investigation', 'get_investigation_view')}
            for tool in discovered:
                assert not {'profile', 'profile_id', 'evidence_dir', 'worker_url', 'token'} & set(tool.input_schema['properties'])
            settings = await success(c, 'workflow_settings')
            assert settings['worker_configured'] is False
            listing = await success(c, 'list_investigations')
            identity = next(row for row in listing['investigations'] if row['bundle_id'] == 'saved.json')
            assert identity['file_sha256'] == sha
            assert str(profiles[0].parent) not in json.dumps(listing)
            inspected = await success(c, 'inspect_investigation', bundle_id='saved.json',
                                      pointer='/payload/runs/0/base_config/prompt', text_limit=2,
                                      expected_file_sha256=sha)
            assert inspected['value'] == 'te' and inspected['next_offset'] == 2
            report = await success(c, 'report_investigation', bundle_id='saved.json',
                                   expected_file_sha256=sha, limit=1)
            assert report['bundle']['file_sha256'] == sha
            assert report['saved_model']['model_id'] == 'test/model'
            assert report['next_offset'] == 1
            observation = report['observations'][0]
            branch = bundle
            for key in observation['pointer'].strip('/').split('/'):
                branch = branch[int(key)] if isinstance(branch, list) else branch[key]
            assert observation['raw_label_counts'] == dict(Counter(branch['answers']))
            assert observation['draw_count'] == len(branch['answers'])
            assert 'synthetic/test' in ' '.join(report['limitations'])
            offset, parts = 0, []
            while offset is not None:
                part = await success(c, 'read_investigation_bytes', bundle_id='saved.json',
                                     offset=offset, length=512, expected_file_sha256=sha)
                assert part['bytes'] <= 512
                parts.append(base64.b64decode(part['data'], validate=True))
                offset = part['next_offset']
            assert len(parts) > 1
            assert b''.join(parts) == raw
            assert hashlib.sha256(b''.join(parts)).hexdigest() == sha
            exported = await success(c, 'export_investigation', bundle_id='saved.json')
            artifact = exported['artifacts'][0]
            path = Path(artifact['path'])
            assert path.is_relative_to(profiles[0].parent / 'exports')
            assert path.read_bytes() == raw
            assert path.stat().st_mode & 0o777 == 0o600
            assert artifact['redacted'] is False
    asyncio.run(asyncio.wait_for(check(), timeout=120))


def test_stdio_rejects_invalid_evidence_requests(protocol_profiles):
    profiles, raw = protocol_profiles
    sha = hashlib.sha256(raw).hexdigest()

    async def check():
        async with client(profiles[0]) as c:
            for name in ('../profile.json', '/tmp/outside.json', 'x\\outside.json'):
                await failure(c, 'inspect_investigation', 'relative JSON', bundle_id=name)
            await failure(c, 'export_investigation', 'Symbolic', bundle_id='cross-profile.json')
            for pointer, message in [('payload', 'RFC 6901'), ('/payload/~2', 'escape'),
                                     ('/payload/runs/01', 'index'), ('/missing', 'not found')]:
                await failure(c, 'inspect_investigation', message, bundle_id='saved.json', pointer=pointer)
            await failure(c, 'inspect_investigation', 'between 1 and 100', bundle_id='saved.json', limit=101)
            await failure(c, 'inspect_investigation', '16000', bundle_id='saved.json', text_limit=16001)
            for arguments, message in [({'length': 49153}, '49152'), ({'offset': -1}, 'nonnegative'),
                                       ({'offset': 1}, 'Continuation'),
                                       ({'offset': len(raw) + 1, 'expected_file_sha256': sha}, 'exceeds')]:
                await failure(c, 'read_investigation_bytes', message, bundle_id='saved.json', **arguments)
            for name in ('inspect_investigation', 'report_investigation', 'read_investigation_bytes'):
                await failure(c, name, 'changed', bundle_id='saved.json', expected_file_sha256='0' * 64)
            # Failure does not poison the session.
            assert (await success(c, 'list_investigations'))['investigations']
    asyncio.run(asyncio.wait_for(check(), timeout=120))


def test_stdio_profile_ownership_and_no_worker_fail_closed(protocol_profiles, config):
    profiles, raw = protocol_profiles

    async def check():
        async with client(profiles[0]) as first, client(profiles[1]) as second:
            assert (await success(first, 'workflow_settings'))['owned_workflow_ids'] == ['b' * 32]
            assert (await success(second, 'workflow_settings'))['owned_workflow_ids'] == []
            listing = await success(second, 'list_investigations')
            assert [row['bundle_id'] for row in listing['investigations']] == ['private.json']
            for name in ('workflow_status', 'cancel_workflow', 'export_workflow'):
                await failure(second, name, 'not owned', workflow_id='b' * 32)
            await failure(second, 'resume_workflow', 'not owned', workflow_id='b' * 32, confirm_execution=True)
            await failure(first, 'workflow_status', 'No worker configured', workflow_id='b' * 32)
            await failure(first, 'start_workflow', 'No worker configured', config=config,
                          request_id='protocol-no-worker', confirm_execution=True)
            await failure(first, 'start_workflow', 'authorization', config=config,
                          request_id='protocol-no-authorization', confirm_execution=False)
    asyncio.run(asyncio.wait_for(check(), timeout=120))
