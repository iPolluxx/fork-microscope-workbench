"""Offline inventory and adapter registration, never a substitute for MCP tests."""
import importlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from offline_support import adapter_context
from discovery import tool_inventory

def test_inventory_matches_adapter_registration():
    with adapter_context():
        server = importlib.import_module('fork_microscope_workbench_mcp')
        assert set(server.mcp.tools) == {t['name'] for t in tool_inventory()}
        assert len(server.mcp.tools) == 13

def test_adapter_context_restores_imports():
    with adapter_context():
        first = importlib.import_module('tools.evidence')
    with adapter_context():
        from tools import evidence
        assert evidence is not first

def test_readiness_does_not_need_runtime_or_expose_profile(tmp_path):
    import os
    env = dict(os.environ, FORK_MICROSCOPE_PROFILE=str(tmp_path / 'missing-secret-profile'))
    result = subprocess.run([sys.executable, str(ROOT / 'src/discovery.py'), '--check'], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report['protocol_verified'] is False
    assert report['chatgpt_connected'] is False
    assert report['transport'] == 'stdio'
    assert str(tmp_path) not in result.stdout
    assert len(report['tools']) == 13
