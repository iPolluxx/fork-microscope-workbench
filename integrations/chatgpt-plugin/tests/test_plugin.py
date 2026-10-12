"""Packaging/private import plus real stdio acceptance; no new inference."""
import asyncio
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("plugin_helper", HERE / "plugin.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
DEMO = helper.REPO / "public/fork-microscope/demo-portfolio.json"
APP = "asdk_app_" + "a" * 32


def test_package_is_deterministic_allowlisted_and_client_specific(tmp_path):
    first = helper.build("plugin_" + APP, tmp_path / "one")
    second = helper.build(APP, tmp_path / "two")
    assert first["archive_sha256"] == second["archive_sha256"]
    with zipfile.ZipFile(first["archive"]) as archive:
        assert set(archive.namelist()) == set(helper.PACKAGE_FILES) | {".app.json"}
        raw = b"".join(archive.read(name) for name in archive.namelist())
        assert str(helper.REPO).encode() not in raw
        assert b"profile.json" not in raw and b"worker-token" not in raw
        app = json.loads(archive.read(".app.json"))
        assert app["apps"]["fork-microscope"]["id"] == APP
        manifest = json.loads(archive.read("plugin.json"))
        overlay = manifest["extensions"]["com.openai"]
        assert overlay["apps"] == "./.app.json"
        assert overlay["onboardingSkill"][2:] in archive.namelist()
        for name, digest in first["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
    root = Path(first["marketplace_root"])
    catalog = json.loads((root / ".agents/plugins/marketplace.json").read_text())
    assert (root / catalog["plugins"][0]["source"]["path"]).is_dir()
    for path in root.rglob("*"):
        assert path.stat().st_mode & 0o077 == 0
    third = helper.build("asdk_app_" + "b" * 32, tmp_path / "three")
    assert third["archive_sha256"] != first["archive_sha256"]
    with pytest.raises(ValueError):
        helper.build(APP, root)


@pytest.mark.parametrize("app", ["", "../profile.json", "https://example.com", "sk-secret", "asdk_app_wrong", APP + "\n"])
def test_invalid_mapping_creates_nothing(tmp_path, app):
    with pytest.raises(ValueError):
        helper.build(app, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_private_output_and_symlink_rejected(tmp_path):
    public = tmp_path / "public"; public.mkdir(mode=0o755)
    with pytest.raises(ValueError):
        helper.build(APP, public)
    alias = tmp_path / "alias"; alias.symlink_to(public, target_is_directory=True)
    with pytest.raises(ValueError):
        helper.build(APP, alias / "nested")
    assert not (public / "nested").exists()


def test_profile_import_preserves_bytes_and_duplicate_is_safe(tmp_path):
    profile = tmp_path / "owner/profile.json"
    initialized = helper.initialize(profile)
    assert initialized["worker_configured"] is False
    result = helper.import_bundle(profile, DEMO)
    raw = DEMO.read_bytes()
    target = profile.parent / "evidence" / result["bundle_id"]
    assert target.read_bytes() == raw
    assert result["file_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["created"] is True
    assert helper.import_bundle(profile, DEMO)["created"] is False
    assert target.stat().st_mode & 0o077 == 0
    assert not list(target.parent.glob(".import-*"))
    original = profile.read_bytes()
    with pytest.raises(subprocess.CalledProcessError):
        helper.initialize(profile)
    assert profile.read_bytes() == original


def test_malformed_tampered_and_symlink_imports_do_not_install(tmp_path):
    profile = tmp_path / "owner/profile.json"; helper.initialize(profile)
    bad = tmp_path / "bad.json"; bad.write_text("{}")
    with pytest.raises(ValueError):
        helper.import_bundle(profile, bad)
    tampered = json.loads(DEMO.read_bytes())
    tampered["payload"]["runs"][0]["base"]["text"] += "changed"
    bad.write_text(json.dumps(tampered))
    with pytest.raises(ValueError):
        helper.import_bundle(profile, bad)
    alias = tmp_path / "alias.json"; alias.symlink_to(DEMO)
    with pytest.raises(OSError):
        helper.import_bundle(profile, alias)
    assert not list((profile.parent / "evidence").iterdir())


def test_environment_excludes_ambient_credentials(monkeypatch):
    for key in ("OPENAI_API_KEY", "RUNPOD_API_KEY", "HF_TOKEN", "FORK_MICROSCOPE_ROOT", "PYTHONPATH"):
        monkeypatch.setenv(key, "must-not-leak")
    env = helper.local_env("/private/profile.json")
    assert "must-not-leak" not in json.dumps(env)
    assert "OPENAI_API_KEY" not in env


def test_generated_launcher_real_stdio_inspects_and_exports_saved_demo(tmp_path):
    pytest.importorskip("fastmcp")
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    profile = tmp_path / "owner/profile.json"
    initialized = helper.initialize(profile)
    imported = helper.import_bundle(profile, DEMO)
    bundle_id = imported["bundle_id"]; digest = imported["file_sha256"]

    async def check():
        transport = StdioTransport(command=initialized["launcher"], args=[], keep_alive=False,
                                   env={"OPENAI_API_KEY": "ambient-do-not-forward"})
        async with Client(transport, timeout=30) as client:
            names = {tool.name for tool in await client.list_tools()}
            assert len(names) == 13
            settings = (await client.call_tool("fork_microscope_workflow_settings", {})).data
            assert settings["worker_configured"] is False
            listing = (await client.call_tool("fork_microscope_list_investigations", {})).data
            assert listing["investigations"][0]["file_sha256"] == digest
            report = (await client.call_tool("fork_microscope_report_investigation", {
                "bundle_id": bundle_id, "limit": 100, "expected_file_sha256": digest})).data
            assert report["next_offset"] is None
            assert report["bundle"]["file_sha256"] == digest
            exported = (await client.call_tool("fork_microscope_export_investigation", {"bundle_id": bundle_id})).data
            assert Path(exported["artifacts"][0]["path"]).read_bytes() == DEMO.read_bytes()
            raw = bytearray(); offset = 0
            while True:
                chunk = (await client.call_tool("fork_microscope_read_investigation_bytes", {
                    "bundle_id": bundle_id, "offset": offset, "expected_file_sha256": digest})).data
                raw.extend(base64.b64decode(chunk["data"]))
                if chunk["next_offset"] is None:
                    break
                offset = chunk["next_offset"]
            assert hashlib.sha256(raw).hexdigest() == digest
            assert raw == DEMO.read_bytes()
    asyncio.run(check())
