"""Public integration smoke and privacy tests; no model or network compute."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tools.profile import load_profile
from fastmcp import Client
from fork_microscope_workbench_mcp import mcp

@pytest.fixture
def profile(tmp_path, monkeypatch):
    path = tmp_path / "private" / "profile.json"
    subprocess.run([sys.executable, str(ROOT / "src/configure.py"), "init", "--profile", str(path)], check=True, capture_output=True)
    monkeypatch.setenv("FORK_MICROSCOPE_PROFILE", str(path))
    return path

def test_private_empty_profile(profile):
    p = load_profile()
    assert p["worker_url"] is None
    assert profile.stat().st_mode & 0o777 == 0o600
    for key in ("evidence_dir", "export_dir", "state_dir"):
        assert p[key].stat().st_mode & 0o777 == 0o700

def test_refuse_reinitialize(profile):
    before = profile.read_bytes()
    result = subprocess.run([sys.executable, str(ROOT / "src/configure.py"), "init", "--profile", str(profile)], capture_output=True)
    assert result.returncode != 0
    assert profile.read_bytes() == before

def test_refuse_public_profile(profile):
    profile.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        load_profile()

def test_refuse_escaping_storage(profile, tmp_path):
    data = json.loads(profile.read_text())
    data["evidence_dir"] = str(tmp_path)
    profile.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="direct children"):
        load_profile()

def test_nine_tools_and_offline_discovery(profile):
    async def check():
        async with Client(mcp) as client:
            names = {t.name for t in await client.list_tools()}
            assert names == {"fork_microscope_" + name for name in (
                "list_investigations", "inspect_investigation", "export_investigation",
                "workflow_settings", "start_workflow", "workflow_status",
                "cancel_workflow", "resume_workflow", "export_workflow")}
            for name in ("list_investigations", "workflow_settings"):
                result = await client.call_tool("fork_microscope_" + name, {})
                assert not result.is_error
    asyncio.run(check())
