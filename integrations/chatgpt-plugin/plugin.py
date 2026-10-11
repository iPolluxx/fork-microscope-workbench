#!/usr/bin/env python3
"""Prepare a private MCP host or package skills for an already-registered app.

Packaging, import and readiness do not contact a worker or execute a model.
Worker setup copies a supplied private token file. No global auto-installation.
`serve` starts the local stdio server, which can execute authorized worker tools.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
MCP = REPO / "integrations/mcp/src"
PACKAGE_FILES = (
    "plugin.json",
    "skills/setup/SKILL.md",
    "skills/fork-investigation/SKILL.md",
)


def private_root(path: Path) -> Path:
    path = path.expanduser().absolute()
    if path.resolve() != path:
        raise ValueError("Private output paths cannot traverse symbolic links.")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Output directory must be owned by you and private (0700).")
    return path


def exclusive_file(path: Path, raw: bytes, mode=0o600) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def local_env(profile: str | Path | None = None) -> dict:
    # No ambient API keys, generic evidence-root variables or user PYTHONPATH.
    env = {"PATH": os.defpath, "PYTHONPATH": os.pathsep.join((str(REPO / "src"), str(MCP))),
           "PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1",
           "TRANSFORMERS_OFFLINE": "1", "FASTMCP_CHECK_FOR_UPDATES": "off"}
    if profile is not None:
        env["FORK_MICROSCOPE_PROFILE"] = str(Path(profile).expanduser().absolute())
    return env


def core_modules(profile: str | Path):
    # Delay workbench imports: packaging needs only the Python standard library.
    sys.path[:0] = [str(REPO / "src"), str(MCP)]
    os.environ["FORK_MICROSCOPE_PROFILE"] = str(Path(profile).expanduser().absolute())
    from tools.profile import load_profile
    return load_profile()


def initialize(profile: str | Path) -> dict:
    path = Path(profile).expanduser().absolute()
    private_root(path.parent)
    subprocess.run([sys.executable, str(MCP / "configure.py"), "init", "--profile", str(path)],
                   env=local_env(), check=True, capture_output=True)
    # Launch helper is outside the distributable plugin, since paths are local.
    script = "#!/bin/sh\nset -eu\nexec " + shlex.join([
        str(Path(sys.executable).absolute()), str(HERE / "plugin.py"), "serve", "--profile", str(path)
    ]) + "\n"
    try:
        exclusive_file(path.parent / "launch-mcp.sh", script.encode(), 0o700)
    except FileExistsError:
        # Never overwrite an operator's launcher. An existing file is a clear
        # setup error, not a reason to undo the newly created private profile.
        raise ValueError("Profile created, but launch-mcp.sh already exists; keep it or choose a new directory.") from None
    return {"profile": str(path), "launcher": str(path.parent / "launch-mcp.sh"),
            "worker_configured": False, "network_contacted": False}


def import_bundle(profile: str | Path, source: str | Path) -> dict:
    config = core_modules(profile)
    from fork_microscope.investigation_bundle import validate_bundle, MAX_IMPORT_BYTES
    path = Path(source).expanduser().absolute()
    # Read the chosen JSON only; never extract an archive or execute its text.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_IMPORT_BYTES:
            raise ValueError("Choose a regular investigation JSON of at most 64 MiB.")
        raw = handle.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise ValueError("Investigation exceeds the upstream size limit.")
    value = validate_bundle(json.loads(raw))
    digest = hashlib.sha256(raw).hexdigest()
    target = config["evidence_dir"] / (digest + ".json")
    created = True
    # Link an already fsynced private file so concurrent readers cannot observe
    # half-written evidence. Exclusive link also makes duplicate imports safe.
    fd, temporary = tempfile.mkstemp(prefix=".import-", dir=config["evidence_dir"])
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError:
            from tools.profile import private
            private(target)
            if target.read_bytes() != raw:
                raise ValueError("Existing evidence conflicts with the content identity.") from None
            created = False
    finally:
        os.unlink(temporary)
    return {"bundle_id": target.name, "file_sha256": digest, "bytes": len(raw),
            "schema": value["schema"], "created": created, "network_contacted": False}


def build(app_id: str, output: str | Path) -> dict:
    # Browser URL uses plugin_asdk_app_*; cached mappings use asdk_app_*.
    app_id = app_id.removeprefix("plugin_")
    if not re.fullmatch(r"asdk_app_[0-9a-f]{32}", app_id):
        raise ValueError("Use the asdk_app_... or plugin_asdk_app_... ID of your registered private MCP app.")
    root = private_root(Path(output))
    package = root / "plugins/fork-microscope"
    # Refuse reusing an output root, including partial builds or symlink targets.
    if any(root.iterdir()):
        raise ValueError("Choose an empty private output directory; builds do not overwrite files.")
    (root / "plugins").mkdir(mode=0o700)
    package.mkdir(mode=0o700)
    files = {}
    for relative in PACKAGE_FILES:
        source = HERE / "package" / relative
        if source.is_symlink() or not source.is_file():
            raise ValueError("Plugin template files must be regular files.")
        files[relative] = source.read_bytes()
    manifest = json.loads(files["plugin.json"])
    manifest["extensions"]["com.openai"]["apps"] = "./.app.json"
    files["plugin.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    files[".app.json"] = (json.dumps({"apps": {"fork-microscope": {"id": app_id, "required": True}}}, indent=2) + "\n").encode()
    for relative, raw in files.items():
        path = package / relative
        parent = package
        for component in Path(relative).parts[:-1]:
            parent = parent / component
            parent.mkdir(mode=0o700, exist_ok=True)
        exclusive_file(path, raw)
    marketplace = {"name": "fork-microscope-private", "plugins": [{
        "name": "fork-microscope", "source": {"source": "local", "path": "./plugins/fork-microscope"},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"
    }]}
    catalog = root / ".agents/plugins/marketplace.json"
    (root / ".agents").mkdir(mode=0o700)
    catalog.parent.mkdir(mode=0o700)
    exclusive_file(catalog, (json.dumps(marketplace, indent=2) + "\n").encode())
    archive = root / "fork-microscope-plugin.zip"
    # Fixed timestamps, sorted allowlist: neither checkout dirt nor data enters.
    with archive.open("xb") as handle:
        os.chmod(archive, 0o600)
        with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED) as out:
            for relative, raw in sorted(files.items()):
                info = zipfile.ZipInfo(relative, (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100600 << 16
                out.writestr(info, raw)
    checksums = {relative: hashlib.sha256(raw).hexdigest() for relative, raw in files.items()}
    result = {"package": str(package), "marketplace_root": str(root), "archive": str(archive),
              "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
              "files": checksums, "registered_app_id": app_id,
              "installed": False, "published": False, "network_contacted": False}
    exclusive_file(root / "build.json", (json.dumps(result, indent=2) + "\n").encode())
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("init", "doctor", "serve"):
        sub.add_parser(action).add_argument("--profile", required=True)
    importer = sub.add_parser("import"); importer.add_argument("--profile", required=True); importer.add_argument("--bundle", required=True)
    worker = sub.add_parser("worker"); worker.add_argument("--profile", required=True); worker.add_argument("--url", required=True); worker.add_argument("--token-file", required=True)
    pack = sub.add_parser("build"); pack.add_argument("--app-id", required=True); pack.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.action == "build":
        result = build(args.app_id, args.output)
    elif args.action == "init":
        result = initialize(args.profile)
    elif args.action == "import":
        result = import_bundle(args.profile, args.bundle)
    elif args.action == "doctor":
        return subprocess.run([sys.executable, str(MCP / "discovery.py"), "--check"], env=local_env(args.profile)).returncode
    elif args.action == "worker":
        return subprocess.run([sys.executable, str(MCP / "configure.py"), "worker", "--profile", str(Path(args.profile).expanduser().absolute()),
                               "--url", args.url, "--token-file", args.token_file], env=local_env()).returncode
    else:
        # Nothing on stdout before exec: it belongs to the MCP JSON-RPC stream.
        core_modules(args.profile)
        os.execve(sys.executable, [sys.executable, str(MCP / "fork_microscope_workbench_mcp.py")], local_env(args.profile))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"Setup failed ({type(exc).__name__}). Check paths, private permissions, bundle validity and the command arguments locally.", file=sys.stderr)
        sys.exit(1)
