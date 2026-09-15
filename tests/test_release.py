"""Distribution invariants: attribution, notices, provenance and stale build rejection."""
import hashlib
import json
from pathlib import Path
import zipfile
import pytest
from scripts.build_dashboard import build, ASSETS
from fork_microscope.upstream_snapshot import verify_snapshot

ROOT=Path(__file__).resolve().parents[1]


def test_static_archive_has_notices_and_only_manifest_files(tmp_path):
    target=build(tmp_path/'dashboard')
    manifest=json.loads((target/'build-manifest.json').read_text())
    assert {'LICENSE','plotly-LICENSE.txt','THIRD-PARTY.md','method-credit.js'} <= manifest.keys()
    assert (target/'LICENSE').read_bytes()==(ROOT/'LICENSE').read_bytes()
    with zipfile.ZipFile(tmp_path/'fork-dashboard.zip') as archive:
        assert set(archive.namelist())==set(manifest)|{'build-manifest.json'}
        assert not any(name.startswith(('.git','vendor/','live-runs/','.agents/')) for name in archive.namelist())
        for name,sha in manifest.items():assert hashlib.sha256(archive.read(name)).hexdigest()==sha
    for name in ('workspace.html','live.html','compare.html','observatory.html','released-data.html'):
        assert 'method-credit.js' in (target/name).read_text()
    assert {'patching-panel.mjs','patching-panel.css'} <= manifest.keys()


def test_worker_serves_inspection_modules_used_by_explorer():
    """A missing allowed module breaks the whole browser import graph."""
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from fork_microscope.microscope_server import Handler
    class QuietHandler(Handler):
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),QuietHandler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        for name in ('patching-panel.mjs','patching-panel.css','lens-panel.mjs','observatory.mjs'):
            with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/{name}') as response:
                assert response.status==200
                assert response.read()==(ROOT/'public/fork-microscope'/name).read_bytes()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


def test_static_build_rejects_leftovers_without_deleting_them(tmp_path):
    target=tmp_path/'dashboard';target.mkdir();secret=target/'private-notes.txt';secret.write_text('private example')
    with pytest.raises(ValueError,match='unexpected'):build(target)
    assert secret.read_text()=='private example'
    assert not (tmp_path/'fork-dashboard.zip').exists()


def test_static_build_rejects_symlink_output(tmp_path):
    actual=tmp_path/'private';actual.mkdir();target=tmp_path/'dashboard';target.symlink_to(actual,target_is_directory=True)
    with pytest.raises(ValueError,match='symlink'):build(target)


def snapshot(root, files):
    revision='a'*40
    value=dict(schema_version=1,commit=revision,files={name:hashlib.sha256(data).hexdigest() for name,data in files.items()})
    (root/'.fork-source.json').write_text(json.dumps(value))
    for name,data in files.items():(root/name).write_bytes(data)
    return revision


def test_git_free_snapshot_detects_changed_or_missing_source(tmp_path):
    revision=snapshot(tmp_path,{'example.py':b'upstream source'})
    verify_snapshot(tmp_path,revision)
    (tmp_path/'example.py').write_bytes(b'changed source')
    with pytest.raises(RuntimeError,match='differs'):verify_snapshot(tmp_path,revision)
    (tmp_path/'example.py').unlink()
    with pytest.raises(RuntimeError,match='unavailable'):verify_snapshot(tmp_path,revision)


def test_snapshot_cannot_escape_root(tmp_path):
    payload=dict(schema_version=1,commit='a'*40,files={'../secret':'b'*64})
    (tmp_path/'.fork-source.json').write_text(json.dumps(payload))
    with pytest.raises(RuntimeError,match='path'):verify_snapshot(tmp_path,'a'*40)
