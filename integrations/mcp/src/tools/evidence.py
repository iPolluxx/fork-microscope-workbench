"""Private-profile adapters for pinned Fork Microscope saved investigation evidence."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import tempfile
from typing import Annotated

from fastmcp import FastMCP
from fork_microscope.investigation_bundle import validate_bundle, MAX_IMPORT_BYTES
from tools.profile import load_profile

REFERENCE = "https://github.com/iPolluxx/fork-microscope-workbench/blob/5c0231c3d64bb8f167e41fea776e4ff2e578d048/src/fork_microscope/investigation_bundle.py"
evidence_mcp = FastMCP(name="evidence")


def _root() -> Path:
    # One process is bound to one private profile by launcher configuration.
    # No MCP argument (or legacy generic-root variable) can select other storage.
    return load_profile()["evidence_dir"]


def _page(offset: int, limit: int) -> None:
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError("offset must be nonnegative; limit must be between 1 and 100.")


def _load(bundle_id: str) -> tuple[dict, bytes]:
    relative = PurePosixPath(bundle_id)
    if not bundle_id or relative.is_absolute() or ".." in relative.parts or "\\" in bundle_id:
        raise ValueError("bundle_id must be a relative JSON path inside the evidence directory.")
    root = _root()
    path = root / relative
    # Refuse symlink hops, even if their present target is within this profile.
    # A client's evidence IDs are names, never a filesystem escape mechanism.
    if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root and parent.is_relative_to(root)):
        raise ValueError("Symbolic links are not accepted for evidence files.")
    path = path.resolve(strict=True)
    if not path.is_relative_to(root) or path.suffix.lower() != ".json" or not path.is_file():
        raise ValueError("Bundle must be a JSON file inside the evidence directory.")
    with path.open("rb") as handle:
        raw = handle.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise ValueError("Bundle exceeds the upstream 64 MiB size limit.")
    value = json.loads(raw)
    return validate_bundle(value), raw


def _identity(bundle_id: str, value: dict, raw: bytes) -> dict:
    manifest = value["manifest"]
    return {
        "bundle_id": bundle_id, "schema": value["schema"],
        "file_sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
        "payload_sha256": value["sha256"],
        "entry_run_id": manifest.get("entry_run_id"),
        "entry_response_id": manifest.get("entry_response_id"),
        "counts": {key: len(value["payload"].get(key, [])) for key in
                   ("runs", "lenses", "patches", "responses", "edits", "captures")},
    }


def _pointer(parent: str, key: str | int) -> str:
    return parent + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _select(value: object, pointer: str) -> object:
    if not pointer:
        return value
    if not pointer.startswith("/"):
        raise ValueError("pointer must be empty or an RFC 6901 JSON Pointer starting with '/'.")
    for part in pointer[1:].split("/"):
        # Reject invalid escape sequences instead of silently selecting another field.
        if re.search(r"~(?![01])", part):
            raise ValueError("Invalid JSON Pointer escape.")
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            if key not in value:
                raise ValueError("JSON Pointer field not found.")
            value = value[key]
        elif isinstance(value, list):
            if not key.isascii() or not key.isdecimal() or (len(key) > 1 and key.startswith("0")) or int(key) >= len(value):
                raise ValueError("JSON Pointer array index not found.")
            value = value[int(key)]
        else:
            raise ValueError("JSON Pointer traverses a scalar.")
    return value


def _preview(value: object, pointer: str) -> dict:
    if isinstance(value, (dict, list, str)):
        kind = "object" if isinstance(value, dict) else "array" if isinstance(value, list) else "string"
        result = {"pointer": pointer, "type": kind, "length": len(value)}
        if isinstance(value, str) and len(value) <= 512:
            result["value"] = value
        else:
            result["summary_only"] = True
        return result
    return {"pointer": pointer, "type": "scalar", "value": value}


@evidence_mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
def fork_microscope_list_investigations(
    offset: Annotated[int, "Zero-based offset in sorted candidate JSON files."] = 0,
    limit: Annotated[int, "Number of candidate files to validate and list, from 1 to 100."] = 20,
) -> dict:
    """List validated saved investigations without model execution.
    Private profile evidence and pagination → bundle metadata and invalid-file diagnostics.
    """
    _page(offset, limit)
    root = _root()
    paths = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.suffix.lower() == ".json")
    entries, invalid = [], []
    for bundle_id in paths[offset:offset + limit]:
        try:
            value, raw = _load(bundle_id)
            entries.append(_identity(bundle_id, value, raw))
        except (ValueError, OSError, TypeError, KeyError) as exc:
            invalid.append({"bundle_id": bundle_id, "error": str(exc), "error_type": type(exc).__name__})
    return {"message": "Saved evidence only. Checksums validate consistency, not scientific truth.",
            "reference": REFERENCE, "artifacts": [], "investigations": entries,
            "invalid_files": invalid, "offset": offset, "limit": limit,
            "total_candidates": len(paths), "next_offset": offset + limit if offset + limit < len(paths) else None}


@evidence_mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
def fork_microscope_inspect_investigation(
    bundle_id: Annotated[str, "Relative JSON bundle identifier returned by list_investigations."],
    pointer: Annotated[str, "RFC 6901 JSON Pointer into native saved evidence; empty opens the envelope."] = "",
    offset: Annotated[int, "Zero-based position in selected object keys, array entries, or string characters."] = 0,
    limit: Annotated[int, "Container page size from 1 to 100."] = 20,
    text_limit: Annotated[int, "String page size in characters, from 1 to 16000."] = 4000,
) -> dict:
    """Navigate saved settings, curves, continuations and lens readouts without recomputation.
    Bundle identifier and JSON Pointer → exact scalar values or paginated children with navigation pointers.
    """
    _page(offset, limit)
    if not 1 <= text_limit <= 16000:
        raise ValueError("text_limit must be between 1 and 16000.")
    value, raw = _load(bundle_id)
    selected = _select(value, pointer)
    result = {"message": "Saved data, not a new model or causal experiment. Navigate child pointers for full values.",
              "reference": REFERENCE, "artifacts": [], "bundle": _identity(bundle_id, value, raw), "pointer": pointer,
              "offset": offset, "limit": limit}
    if isinstance(selected, (dict, list)):
        items = list(selected.items()) if isinstance(selected, dict) else list(enumerate(selected))
        result.update(type="object" if isinstance(selected, dict) else "array", total=len(items),
                      entries=[dict(key=key, **_preview(child, _pointer(pointer, key))) for key, child in items[offset:offset + limit]])
    elif isinstance(selected, str):
        limit = text_limit
        result.update(type="string", total=len(selected), limit=limit, value=selected[offset:offset + limit])
    else:
        if offset:
            raise ValueError("Scalar values require offset=0.")
        result.update(type="scalar", total=1, value=selected)
    result["next_offset"] = offset + limit if offset + limit < result["total"] else None
    result["truncated"] = bool(offset or result["next_offset"] is not None)
    return result


@evidence_mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False})
def fork_microscope_export_investigation(
    bundle_id: Annotated[str, "Relative JSON identifier of the validated bundle to export unchanged."],
) -> dict:
    """Export one complete saved investigation with its original bytes and checksum unchanged.
    Bundle identifier → fresh artifact path, file SHA-256, payload checksum and evidence counts.
    """
    value, raw = _load(bundle_id)
    output = load_profile()["export_dir"]
    directory = Path(tempfile.mkdtemp(prefix="investigation-", dir=output))
    artifact = directory / "investigation.json"
    fd = os.open(artifact, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
    return {"message": "Complete validated bundle exported byte-for-byte; no evidence was recomputed or omitted.",
            "reference": REFERENCE, "artifacts": [{"description": "Portable original investigation bundle", "path": str(artifact)}],
            "bundle": _identity(bundle_id, value, raw)}
