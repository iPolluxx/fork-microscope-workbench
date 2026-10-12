"""Exercise the actual pilot package, including the complete bundled viewer."""
import hashlib
import importlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integrations/mcp/tests'))

from offline_support import adapter_context
from scripts.build_pilot_context import ROOT, prepare


def test_pilot_context_can_bundle_exact_reviewed_viewer(tmp_path, monkeypatch):
    context = prepare(tmp_path / 'pilot-context')
    manifest = json.loads((context / 'build-manifest.json').read_text())
    measurement = context / 'mcp/ui/measurements.mjs'
    assert measurement.read_bytes() == (ROOT / 'integrations/mcp/ui/measurements.mjs').read_bytes()
    assert manifest['mcp/ui/measurements.mjs'] == hashlib.sha256(measurement.read_bytes()).hexdigest()
    with adapter_context():
        viewer = importlib.import_module('tools.viewer')
        source_html = viewer.VIEWER_HTML
        monkeypatch.setattr(viewer, 'UI', context / 'mcp/ui')
        assert viewer.build_viewer_html() == source_html
    assert not list(context.rglob('.git'))
    assert not list(context.rglob('.env'))
    assert sorted(p.relative_to(context).as_posix() for p in context.rglob('*.json')) == ['build-manifest.json']
