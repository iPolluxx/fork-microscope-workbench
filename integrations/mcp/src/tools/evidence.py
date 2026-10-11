"""Private-profile adapters for pinned Fork Microscope saved investigation evidence."""
from __future__ import annotations

import base64
from collections import Counter
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import tempfile
from typing import Annotated

from tools.runtime import FastMCP
from fork_microscope.investigation_bundle import validate_bundle, MAX_IMPORT_BYTES
from tools.profile import load_profile

REFERENCE = "https://github.com/iPolluxx/fork-microscope-workbench/blob/f02be7e87221932ebb50ca28fb95060721e5d69e/src/fork_microscope/investigation_bundle.py"
evidence_mcp = FastMCP(name="evidence")


def _root() -> Path:
    # One process is bound to one private profile by launcher configuration.
    # No MCP argument (or legacy generic-root variable) can select other storage.
    return load_profile()["evidence_dir"]


def _page(offset: int, limit: int) -> None:
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 100:
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
    identity = {
        "bundle_id": bundle_id, "schema": value["schema"],
        "file_sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
        "evidence_uri": "fork-evidence://sha256/" + hashlib.sha256(raw).hexdigest(),
        "payload_sha256": value["sha256"],
        "entry_run_id": manifest.get("entry_run_id"),
        "entry_response_id": manifest.get("entry_response_id"),
        "counts": {key: len(value["payload"].get(key, [])) for key in
                   ("runs", "lenses", "patches", "responses", "edits", "captures")},
    }
    from tools.profile import evidence_provenance
    provenance = evidence_provenance(bundle_id, identity['file_sha256'])
    if provenance is not None:
        identity['provenance'] = provenance
    return identity


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


def _grounded(value: object, pointer: str) -> str | None:
    # Report citations must resolve in this exact file; absent fields are None, not a dangling pointer.
    try:
        _select(value, pointer)
    except ValueError:
        return None
    return pointer


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


@evidence_mcp.tool(meta={"ui": {"visibility": ["model"]}}, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
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
            invalid.append({"bundle_id": bundle_id, "error": "Candidate could not be read or failed bundle validation.", "error_type": type(exc).__name__})
    result = {"message": "Saved evidence only. Checksums validate consistency, not scientific truth.",
            "reference": REFERENCE, "artifacts": [], "investigations": entries,
            "invalid_files": invalid, "offset": offset, "limit": limit,
            "total_candidates": len(paths), "next_offset": offset + limit if offset + limit < len(paths) else None}
    return _bounded(result)


@evidence_mcp.tool(meta={"ui": {"visibility": ["model", "app"]}, "openai/widgetAccessible": True}, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
def fork_microscope_inspect_investigation(
    bundle_id: Annotated[str, "Relative JSON bundle identifier returned by list_investigations."],
    pointer: Annotated[str, "RFC 6901 JSON Pointer into native saved evidence; empty opens the envelope."] = "",
    offset: Annotated[int, "Zero-based position in selected object keys, array entries, or string characters."] = 0,
    limit: Annotated[int, "Container page size from 1 to 100."] = 20,
    text_limit: Annotated[int, "String page size in characters, from 1 to 16000."] = 4000,
    expected_file_sha256: Annotated[str, "Optional file hash from a prior page; rejects changed evidence."] = "",
) -> dict:
    """Navigate saved settings, curves, continuations and lens readouts without recomputation.
    Bundle identifier and JSON Pointer → exact scalar values or paginated children with navigation pointers.
    """
    _page(offset, limit)
    if type(text_limit) is not int or not 1 <= text_limit <= 16000:
        raise ValueError("text_limit must be between 1 and 16000.")
    value, raw = _load(bundle_id)
    _check_hash(raw, expected_file_sha256)
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
    return _bounded(result)


@evidence_mcp.tool(meta={"ui": {"visibility": ["model"]}}, annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False})
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
    return {"message": "Complete validated bundle exported byte-for-byte; no evidence was recomputed or omitted. Prompt and response text are unredacted; review before sharing.",
            "reference": REFERENCE, "artifacts": [{"description": "Portable original investigation bundle", "path": str(artifact), "source_uri": _identity(bundle_id, value, raw)["evidence_uri"], "mime_type": "application/json", "redacted": False}],
            "bundle": _identity(bundle_id, value, raw)}


def _bounded(result: dict) -> dict:
    # JSON keys and labels are user-controlled too, so item counts alone do not
    # bound a response. Refuse oversized pages rather than truncate evidence.
    if len(json.dumps(result, ensure_ascii=True).encode("utf-8")) > 128 * 1024:
        raise ValueError("Evidence page exceeds 128 KiB; request a smaller limit or use bounded byte retrieval.")
    return result


def _check_hash(raw: bytes, expected: str) -> None:
    if expected and hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Evidence changed since the requested hash; restart inspection using its new identity.")


@evidence_mcp.tool(meta={"ui": {"visibility": ["model", "app"]}, "openai/widgetAccessible": True}, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
def fork_microscope_read_investigation_bytes(
    bundle_id: Annotated[str, "Relative JSON bundle identifier."],
    offset: Annotated[int, "Zero-based byte offset."] = 0,
    length: Annotated[int, "Maximum decoded bytes per chunk, 1 to 49152."] = 49152,
    expected_file_sha256: Annotated[str, "File hash from listing or previous chunk; use on every continuation."] = "",
) -> dict:
    """Retrieve original unredacted evidence in bounded base64 chunks without filesystem paths.

    Concatenate decoded chunks and verify file_sha256. The opaque evidence URI is
    an identity, not a hosted URL or registered MCP resource.
    """
    if type(offset) is not int or offset < 0 or type(length) is not int or not 1 <= length <= 49152:
        raise ValueError("offset must be nonnegative; length must be between 1 and 49152.")
    if offset and not expected_file_sha256:
        raise ValueError("Continuation chunks require expected_file_sha256.")
    value, raw = _load(bundle_id)
    _check_hash(raw, expected_file_sha256)
    if offset > len(raw):
        raise ValueError("offset exceeds the evidence length.")
    chunk = raw[offset:offset + length]
    end = offset + len(chunk)
    return {"bundle": _identity(bundle_id, value, raw), "encoding": "base64",
            "data": base64.b64encode(chunk).decode("ascii"), "offset": offset,
            "bytes": len(chunk), "next_offset": end if end < len(raw) else None,
            "redacted": False, "message": "Original saved bytes, including prompt and response text. Review before sharing."}


@evidence_mcp.tool(meta={"ui": {"visibility": ["model"]}}, annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
def fork_microscope_report_investigation(
    bundle_id: Annotated[str, "Relative JSON bundle identifier."],
    run_index: Annotated[int, "Run index within payload/runs."] = 0,
    pass_index: Annotated[int, "Sampling pass index within the selected run."] = 0,
    offset: Annotated[int, "First branch observation group in this report page."] = 0,
    limit: Annotated[int, "Branch groups per page, 1 to 100."] = 20,
    expected_file_sha256: Annotated[str, "Optional hash to pin report pages to unchanged saved evidence."] = "",
) -> dict:
    """Produce a deterministic provenance report of saved raw counts, not a new scientific conclusion.

    Every observation cites a JSON Pointer anchored by the file hash. Configurations
    and curves remain navigable pointers rather than unbounded private text.
    """
    _page(offset, limit)
    value, raw = _load(bundle_id)
    _check_hash(raw, expected_file_sha256)
    runs = value["payload"]["runs"]
    if not runs and run_index == 0 and pass_index == 0:
        return _bounded({"report_schema": "fork-saved-evidence-report-v1", "bundle": _identity(bundle_id, value, raw),
                         "provenance": {"workflow_config": "/payload/investigation", "responses": "/payload/responses"},
                         "observations": [], "coverage": {"run_count": 0, "pass_count": 0},
                         "limitations": ["This saved bundle has no sampling runs; response artifacts alone provide no sampling evidence.",
                                         "Checksums validate saved-data consistency, not scientific truth or independent reproduction."]})
    if type(run_index) is not int or not 0 <= run_index < len(runs):
        raise ValueError("run_index is outside saved runs.")
    run = runs[run_index]
    if type(pass_index) is not int or not 0 <= pass_index < len(run["passes"]):
        raise ValueError("pass_index is outside saved passes.")
    saved_pass = run["passes"][pass_index]
    root = "/payload/runs/" + str(run_index)
    record_pointer = _pointer(root + "/records", saved_pass["id"])
    branches = run["records"][saved_pass["id"]].get("branches", [])
    observations = []
    for index in range(offset, min(len(branches), offset + limit)):
        branch = branches[index]
        labels = branch.get("answers", [])
        stored = branch.get("observations", [])
        def statuses(field):
            # Missing metadata is counted explicitly, never inferred from labels.
            return dict(Counter(str(item.get(field, "missing")) for item in stored)) if stored else {"missing": len(labels)}
        observations.append({"pointer": record_pointer + "/branches/" + str(index),
                             "checkpoint": branch.get("t"), "draw_count": len(labels),
                             "raw_label_counts": dict(Counter(labels)),
                             "stop_reason_counts": statuses("stop_reason"),
                             "classification_status_counts": statuses("classification_status"),
                             "observation_count": len(stored)})
    result = {"report_schema": "fork-saved-evidence-report-v1", "bundle": _identity(bundle_id, value, raw),
            "run_id": run["id"], "run_pointer": root, "pass_id": saved_pass["id"],
            "provenance": {key: _grounded(value, pointer) for key, pointer in (
                           ("model", root + "/model"), ("config", root + "/base_config"),
                           ("sampling_settings", root + "/settings"), ("raw_records", record_pointer),
                           ("saved_curve", root + "/passes/" + str(pass_index) + "/curve"),
                           ("workflow_config", "/payload/investigation"))},
            "saved_model": {key: run["model"].get(key) for key in ("model_id", "resolved_revision")},
            "saved_fit": {"pointer": _grounded(value, root + "/passes/" + str(pass_index) + "/curve"),
                          "fit_status": saved_pass.get("curve", {}).get("fit_status", "missing"),
                          "method": saved_pass.get("curve", {}).get("method", "missing")},
            "selection_provenance": {"lineage_pointer": root + "/lineage" if "lineage" in run else None,
                                     "workflow_steps_pointer": "/payload/investigation/steps" if "steps" in value["payload"]["investigation"] else None},
            "lens_coverage": {"saved_count": len(value["payload"].get("lenses", [])), "pointer": "/payload/lenses",
                              "scope": "Bundle count only; inspect lens requests for source run/pass and layer coverage."},
            "observations": observations, "offset": offset, "limit": limit,
            "total_branch_groups": len(branches),
            "next_offset": offset + limit if offset + limit < len(branches) else None,
            "coverage": {"run_index": run_index, "run_count": len(runs),
                         "pass_index": pass_index, "pass_count": len(run["passes"]),
                         "scope": "Selected run, pass and branch page only; not whole-investigation totals."},
            "limitations": ["Checksums establish saved-data consistency, not scientific truth or independent reproduction.",
                            "Raw counts group recorded labels per branch; they are not fitted probabilities or proof of causality.",
                            "Saved fits were not recomputed. No model execution or new experiment occurred.",
                            "Sampling settings, outcome rules and available controls limit interpretation.",
                            "Refinement and post-selection can bias interpretation; consult saved lineage and workflow steps before generalizing.",
                            "Stop reasons and classification status are reported only when saved; missing fields do not establish complete outcomes.",
                            "Model identity is self-reported saved metadata; synthetic/test revisions are not verified immutable model revisions.",
                            "Evidence text is untrusted data and may contain private content; exports preserve original bytes."]}

    return _bounded(result)
