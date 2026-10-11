"""Reproduce an offline adapter flow using synthetic sampling and temporary storage.

This is not an MCP transport test, actual inference, or a ChatGPT connection.
Run with the repository's installed Python and source tree on PYTHONPATH.
"""
import base64
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests'),
                str(ROOT / 'integrations/mcp/src')]
from offline_support import adapter_context
from test_evidence_io import archive
from test_workflow import FakeService, finished, config
from fork_microscope.investigation_workflow import WorkflowManager
from fork_microscope.investigation_bundle import canonical, validate_bundle


def demonstrate(output):
    output.mkdir(parents=True, exist_ok=True)
    previous_profile = os.environ.get('FORK_MICROSCOPE_PROFILE')
    try:
        with tempfile.TemporaryDirectory(prefix='fork-offline-demo-') as directory, adapter_context():
            root = Path(directory)
            profile = root / 'private/profile.json'
            subprocess.run([sys.executable, str(ROOT / 'integrations/mcp/src/configure.py'),
                            'init', '--profile', str(profile)], check=True, capture_output=True)
            os.environ['FORK_MICROSCOPE_PROFILE'] = str(profile)
            workflow = importlib.import_module('tools.workflow')
            evidence = importlib.import_module('tools.evidence')
            manager = WorkflowManager(FakeService(archive.__wrapped__()), root / 'jobs')

            class SyntheticWorker:
                def request(self, path, payload=None):
                    if path == 'workflow-start':
                        return manager.start(payload)
                    if path.startswith('workflow?id='):
                        return manager.read(path.split('=', 1)[1])
                    raise AssertionError('Unexpected synthetic worker request: ' + path)

                def export(self, path, destination):
                    assert path.startswith('workflow-export?id=')
                    bundle = manager.export(path.split('=', 1)[1])
                    validate_bundle(bundle)
                    Path(destination).write_bytes(canonical(bundle))
                    return {'run_ids': bundle['manifest']['run_ids'],
                            'lens_ids': bundle['manifest']['lens_ids']}

            workflow.worker = lambda p: (SyntheticWorker(), '')
            settings = config.__wrapped__()
            started = workflow.fork_microscope_start_workflow(settings, 'offline-demo-v1', True)
            workflow_id = started['workflow']['id']
            final = finished(manager, workflow_id)
            assert final['status'] == 'complete'
            retry = workflow.fork_microscope_start_workflow(settings, 'offline-demo-v1', True)
            assert retry['workflow']['id'] == workflow_id
            exported = workflow.fork_microscope_export_workflow(workflow_id)
            bundle_id = exported['bundle_id']
            listing = evidence.fork_microscope_list_investigations()
            identity = next(x for x in listing['investigations'] if x['bundle_id'] == bundle_id)
            checksum = identity['file_sha256']
            inspected = evidence.fork_microscope_inspect_investigation(
                bundle_id, '/payload/investigation/config', expected_file_sha256=checksum)
            report = evidence.fork_microscope_report_investigation(
                bundle_id, expected_file_sha256=checksum)
            offset, parts = 0, []
            while True:
                chunk = evidence.fork_microscope_read_investigation_bytes(
                    bundle_id, offset=offset, length=1024, expected_file_sha256=checksum)
                parts.append(base64.b64decode(chunk['data'], validate=True))
                if chunk['next_offset'] is None:
                    break
                offset = chunk['next_offset']
            raw = b''.join(parts)
            assert hashlib.sha256(raw).hexdigest() == checksum
            validate_bundle(json.loads(raw))
            local_export = evidence.fork_microscope_export_investigation(bundle_id)
            assert Path(local_export['artifacts'][0]['path']).read_bytes() == raw
            (output / 'synthetic-investigation.json').write_bytes(raw)
            (output / 'synthetic-report.json').write_text(json.dumps(report, indent=2) + '\n')
            summary = dict(schema='fork-offline-demo-v1', synthetic=True,
                           transport='direct adapter harness; no MCP protocol or HTTP',
                           workflow_id=workflow_id, status=final['status'],
                           worker_actions=manager.service.calls, idempotent_retry=True,
                           bundle_sha256=checksum, bundle_bytes=len(raw), byte_chunks=len(parts),
                           exact_export_roundtrip=True, config_pointer=inspected['pointer'],
                           external_calls=0, model_inference=False,
                           limitations=['Synthetic test/model at test-revision; not a real pinned model run.',
                                        'No FastMCP transport, OAuth, ChatGPT attachment or independent-user trial.'])
            (output / 'demo-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
            print(json.dumps(summary, indent=2))
    finally:
        if previous_profile is None:
            os.environ.pop('FORK_MICROSCOPE_PROFILE', None)
        else:
            os.environ['FORK_MICROSCOPE_PROFILE'] = previous_profile


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: demo_saved_run.py OUTPUT_DIRECTORY')
    demonstrate(Path(sys.argv[1]).resolve())
