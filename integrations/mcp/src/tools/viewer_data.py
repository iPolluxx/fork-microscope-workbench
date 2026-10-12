"""Bounded, hash-pinned saved evidence for the graph viewer; no model execution.

All strings are untrusted evidence. Consumers must render them as text, never HTML.
The envelope validator establishes saved-data consistency, not scientific truth.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import re

from tools import evidence

SCHEMA = "fork-microscope-view-v1"
DESIGN = "position_mixture_v1"
MAX_CHECKPOINTS = 128
TEXT_LIMIT = 2000
BASE_TEXT_LIMIT = 4000
MAX_CONTEXT_TOKENS = 256
TOKEN_TEXT_LIMIT = 128
MAX_FIT_POINTS = 512
MAX_RECONSTRUCTION_BYTES = 48 * 1024


def _whole(value: object) -> bool:
    return type(value) is int and value >= 0


def _text(value: object, pointer: str, limit: int = BASE_TEXT_LIMIT) -> dict:
    present = isinstance(value, str)
    return {"text": value[:limit] if present else None,
            "truncated": present and len(value) > limit,
            "missing": not present, "pointer": pointer,
            "total_characters": len(value) if present else None}


def _probabilities(value: object, categories: list[str]) -> bool:
    return (isinstance(value, list) and len(value) == len(categories)
            and all(type(x) in (int, float) and math.isfinite(x) and 0 <= x <= 1 for x in value)
            and abs(sum(value) - 1) <= 1e-6)


def _checkpoint_step(run: dict, chosen: dict) -> int | None:
    """Use a recorded pass stride, never array-index distance or text length."""
    configuration = chosen.get("configuration")
    if isinstance(configuration, dict) and "stride" in configuration:
        stride = configuration["stride"]
    else:
        configured = next((item for item in run.get("settings", {}).get("passes", [])
                           if isinstance(item, dict) and item.get("id") == chosen["id"]), {})
        stride = configured.get("stride")
    if stride is None:
        return None
    if not _whole(stride) or stride < 1:
        raise ValueError("Saved checkpoint stride must be a positive integer.")
    return stride


def _token_context(base: dict, root: str, selected: int, step: int | None) -> dict:
    tokens = base["tokens"]
    start = 0 if len(tokens) <= MAX_CONTEXT_TOKENS else max(0, selected - 34)
    end = len(tokens) if len(tokens) <= MAX_CONTEXT_TOKENS else min(len(tokens), start + MAX_CONTEXT_TOKENS)
    pointer = root + "/base/tokens"
    items = []
    for t in range(start, end):
        preview = _text(tokens[t], pointer + "/" + str(t), TOKEN_TEXT_LIMIT)
        items.append({"t": t, "text": preview["text"], "pointer": preview["pointer"],
                      "text_truncated": preview["truncated"],
                      "total_characters": preview["total_characters"]})
    window_truncated = start > 0 or end < len(tokens)
    text_truncated = any(item["text_truncated"] for item in items)
    return {"items": items, "token_start": start, "token_end": end,
            "total_tokens": len(tokens), "truncated": window_truncated or text_truncated,
            "window_truncated": window_truncated, "text_truncated": text_truncated,
            "concatenates_to_response": "".join(tokens) == base["text"], "pointer": pointer,
            "limitations": ["These are saved token strings, not a tokenizer reconstruction; truncated strings are marked."]}


def _saved_reconstruction(run: dict, chosen: dict, root: str, pass_index: int,
                          checkpoints: list[dict], step: int | None) -> dict:
    """Expose saved fit values only after matching their input to verified draws.

    No fit, interpolation, renormalization, downsampling or model call occurs.
    An unsupported fit leaves the observed counts available.
    """
    result = {"available": False, "reason": "No supported saved reconstruction is available.",
              "points": [], "provenance": None, "limitations": []}
    curve = chosen.get("curve")
    if not isinstance(curve, dict) or curve.get("fit_status") != "complete":
        result["reason"] = "The saved reconstruction is absent, incomplete or withheld; observed counts remain available."
        return result
    if step is None:
        result["reason"] = "No recorded integer comparison horizon is available for this saved fit."
        return result
    parameters = curve.get("parameters")
    upstream = run.get("upstream_commit")
    version = run.get("versions", {}).get("otrecon")
    if (not isinstance(parameters, dict) or parameters.get("variant") not in ("linear", "mult")
            or any(type(parameters.get(key)) not in (int, float) or not math.isfinite(parameters[key])
                   or parameters[key] <= 0 for key in ("h", "pen"))
            or curve.get("tuning") not in ("cv", "fixed")
            or not isinstance(upstream, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", upstream)
            or not isinstance(version, str) or not version):
        result["reason"] = "Saved reconstruction method parameters or recorded revision provenance are missing or unsupported."
        return result
    positions, weighted = curve.get("positions"), curve.get("weighted")
    if (positions != [point["t"] for point in checkpoints] or not isinstance(weighted, list)
            or len(weighted) != len(checkpoints)
            or any(not _probabilities(values, run["categories"])
                   or any(abs(value - point["counts"][category] / point["n"]) > 1e-6
                          for value, category in zip(values, run["categories"]))
                   for values, point in zip(weighted, checkpoints))):
        result["reason"] = "Saved reconstruction inputs do not match the verified observed checkpoint counts."
        return result
    support, smoothed = curve.get("support"), curve.get("smoothed")
    if (not isinstance(support, list) or not isinstance(smoothed, list) or not support
            or len(support) != len(smoothed) or len(support) > MAX_FIT_POINTS
            or any(not _whole(t) or not checkpoints[0]["t"] <= t <= checkpoints[-1]["t"] for t in support)
            or support != sorted(set(support))
            or any(not _probabilities(values, run["categories"]) for values in smoothed)):
        result["reason"] = "Saved reconstruction coordinates or probability rows are invalid or exceed this bounded viewer."
        return result
    if support != list(range(checkpoints[0]["t"], checkpoints[-1]["t"] + 1)):
        result["reason"] = "Saved reconstruction does not contain a dense per-token curve across the observed range; gaps are not interpolated."
        return result
    support_set = set(support)
    if not any(t + step in support_set for t in support):
        result["reason"] = "Saved fit support contains no exact t to t+N comparison for the recorded horizon."
        return result
    curve_pointer = root + "/passes/" + str(pass_index) + "/curve"
    low, high = curve.get("low"), curve.get("high")
    has_bands = (curve.get("bands_kind") == "model_based_dirichlet_marginal"
                 and isinstance(low, list) and isinstance(high, list)
                 and len(low) == len(high) == len(support)
                 and all(isinstance(lo, list) and isinstance(hi, list)
                         and len(lo) == len(hi) == len(run["categories"])
                         and all(type(a) in (int, float) and type(b) in (int, float)
                                 and math.isfinite(a) and math.isfinite(b) and 0 <= a <= b <= 1
                                 for a, b in zip(lo, hi)) for lo, hi in zip(low, high)))
    points = []
    for index, (t, values) in enumerate(zip(support, smoothed)):
        point = {"t": t, "probabilities": values, "pointer": curve_pointer + "/smoothed/" + str(index),
                 "coordinate_pointer": curve_pointer + "/support/" + str(index)}
        if has_bands:
            point.update(low=low[index], high=high[index], bands_pointer={
                "low": curve_pointer + "/low/" + str(index), "high": curve_pointer + "/high/" + str(index)})
        points.append(point)
    result.update(available=True, reason=None, points=points,
                  provenance={"method": "M5a_segkernel", "method_recorded": False,
                              "method_basis": "Application reconstruction contract for position_mixture_v1 exports; the estimator name is not a recorded curve field.",
                              "parameters": parameters, "tuning": curve["tuning"], "fit_status": curve["fit_status"],
                              "upstream_commit": upstream, "otrecon_version": version,
                              "pointer": curve_pointer, "parameters_pointer": curve_pointer + "/parameters",
                              "upstream_pointer": root + "/upstream_commit", "version_pointer": root + "/versions/otrecon",
                              "bands_kind": curve.get("bands_kind") if has_bands else None},
                  limitations=["Saved reconstruction values are model estimates, not additional observed samples or proof of a causal decision point.",
                               "Only exact saved t to t+N endpoints are compared; gaps are not interpolated or extrapolated.",
                               "Nominal uncertainty bands are model-based, may under-cover, and exclude exactly zero and one.",
                               *curve.get("warnings", []), *run.get("caveats", [])])
    if not has_bands:
        result["limitations"].append("Valid saved uncertainty bands are unavailable; no bands are invented.")
    if len(json.dumps(result, ensure_ascii=True).encode()) > MAX_RECONSTRUCTION_BYTES:
        return {"available": False, "reason": "The complete saved fit exceeds this bounded viewer; inspect its source pointer.",
                "points": [], "provenance": {"pointer": curve_pointer}, "limitations": []}
    return result


def _row(branch: dict, branch_pointer: str, index: int) -> dict:
    observation = branch["observations"][index]
    pointer = evidence._pointer(branch_pointer + "/observations", index)
    preview = _text(observation.get("continuation_text"), pointer + "/continuation_text", TEXT_LIMIT)
    return {"draw_index": branch["draw_indices"][index], "label": observation["label"],
            "label_source": observation.get("label_source"),
            "classification_status": observation.get("classification_status"),
            "stop_reason": observation.get("stop_reason"),
            "stop_reason_evidence": observation.get("stop_reason_evidence"),
            "generated_tokens": observation.get("generated_tokens"),
            "tok_id": branch.get("tok_id"), "is_base": branch.get("is_base"),
            "text": preview["text"], "text_truncated": preview["truncated"],
            "text_missing": preview["missing"], "text_characters": preview["total_characters"],
            "pointer": pointer, "text_pointer": preview["pointer"]}


def _contrast(rows: list[dict]) -> list[dict]:
    result, labels = [], set()
    for row in rows:
        # Missing/incomplete metadata cannot stand in for a completed contrast.
        if (row["label"] == "Other" or row["label"] in labels or row["text_missing"]
                or row["stop_reason"] != "eos" or row["label_source"] in ("incomplete", "ambiguous", "unparsed")):
            continue
        result.append(row)
        labels.add(row["label"])
        if len(result) == 2:
            return result
    return []


def _checkpoint_rows(record: dict, record_pointer: str, categories: list[str]) -> tuple[list[dict], dict[int, list[dict]]]:
    positions = record.get("positions", [])
    by_position = {}
    for position_index, position in enumerate(positions):
        t, n = position.get("t"), position.get("samples")
        if not _whole(t) or t in by_position or not _whole(n) or n < 1:
            raise ValueError("Saved checkpoint coordinates or sample counts are invalid.")
        by_position[t] = (position_index, position, {})
    for branch_index, branch in enumerate(record.get("branches", [])):
        t = branch.get("t")
        if t not in by_position:
            raise ValueError("Saved continuation refers to a missing checkpoint.")
        indices, labels, observations = (branch.get(key, []) for key in ("draw_indices", "answers", "observations"))
        if len(indices) != len(labels) or len(labels) != len(observations):
            raise ValueError("Saved continuation metadata is incomplete.")
        draws = by_position[t][2]
        for index, (draw, label, observation) in enumerate(zip(indices, labels, observations)):
            if (not _whole(draw) or draw in draws or label not in categories
                    or not isinstance(observation, dict) or observation.get("label") != label):
                raise ValueError("Saved continuation indices or labels disagree.")
            draws[draw] = _row(branch, record_pointer + "/branches/" + str(branch_index), index)
    checkpoints, grouped = [], {}
    for t in sorted(by_position):
        position_index, position, draws = by_position[t]
        n = position["samples"]
        if len(draws) != n or any(index not in draws for index in range(n)):
            raise ValueError("Every saved checkpoint draw must appear exactly once.")
        rows = grouped[t] = [draws[index] for index in range(n)]
        counts = Counter(row["label"] for row in rows)
        mass = position.get("retained_mass")
        if type(mass) not in (int, float) or not math.isfinite(mass) or not 0 <= mass <= 1:
            mass = None
        pointer = record_pointer + "/positions/" + str(position_index)
        candidates = position.get("candidates")
        checkpoints.append({"t": t, "counts": {label: counts[label] for label in categories},
                            "n": n, "retained_mass": mass,
                            "candidates": len(candidates) if isinstance(candidates, list) else None,
                            "counts_kind": "raw_sampled_frequencies", "pointer": pointer,
                            "n_pointer": pointer + "/samples", "counts_source_pointer": record_pointer + "/branches",
                            "candidates_pointer": pointer + "/candidates" if isinstance(candidates, list) else None})
    for index, point in enumerate(checkpoints):
        point["next_observed_t"] = checkpoints[index + 1]["t"] if index + 1 < len(checkpoints) else None
        point["previous_observed_t"] = checkpoints[index - 1]["t"] if index else None
    return checkpoints, grouped


def get_investigation_view(
    bundle_id: str, run_index: int = 0, pass_index: int = 0,
    token_position: int | None = None, offset: int = 0, limit: int = 5,
    expected_file_sha256: str = "",
) -> dict:
    """Project validated saved evidence into a bounded graph-viewer page.

    Pass the returned bundle.file_sha256 on every follow-up call. Continuation
    pages require a hash; all supplied hashes reject changed original bytes.
    Text previews are exact prefixes of saved strings. Full strings and omitted
    checkpoint metadata remain available through the existing inspect tool.
    """
    evidence._page(offset, limit)
    if limit > 20:
        raise ValueError("Viewer continuation limit must be between 1 and 20.")
    if not _whole(run_index) or not _whole(pass_index):
        raise ValueError("run_index and pass_index must be nonnegative integers.")
    if token_position is not None and not _whole(token_position):
        raise ValueError("token_position must be a nonnegative integer or null.")
    if not isinstance(expected_file_sha256, str):
        raise ValueError("expected_file_sha256 must be a string.")
    if offset and not expected_file_sha256:
        raise ValueError("Continuation pages require expected_file_sha256.")
    value, raw = evidence._load(bundle_id)
    evidence._check_hash(raw, expected_file_sha256)
    identity = evidence._identity(bundle_id, value, raw)
    result = {
        "schema": SCHEMA, "status": "empty", "bundle": identity,
        "selection": {"run_index": run_index, "pass_index": pass_index, "run_id": None,
                      "response_id": None, "pass_id": None, "token_position": None},
        "run": None, "checkpoints": [], "checkpoints_total": 0, "checkpoints_truncated": False,
        "selected": None,
        "reconstruction": {"available": False, "reason": "No usable observed sampling pass is selected.",
                           "points": [], "provenance": None, "limitations": []},
        "readouts": {"lens_count": identity["counts"]["lenses"],
                     "capture_count": identity["counts"]["captures"],
                     "patch_count": identity["counts"]["patches"], "scope": "bundle"},
        "inspect": {"tool": "fork_microscope_inspect_investigation",
                    "bundle_id": bundle_id, "expected_file_sha256": identity["file_sha256"]},
        "limitations": [
            "Saved evidence only; no model execution or runtime readiness check occurred.",
            "Checksums verify saved-data consistency, not scientific truth or independent reproduction.",
            "Outcome matches are labels, not correctness or proof of a causal decision point.",
            "Raw observed frequencies are conditional on the retained next-token candidates; omitted mass is not an outcome category.",
            "Continuation text starts after the forced branch token; that token is identified by its saved token ID, not an inferred decoded string.",
            "Other can include unparsed, ambiguous or incomplete continuations; stop_reason and label_source remain separate.",
            "Raw checkpoints are observed samples. Smoothed mode is available only for a supported saved reconstruction with recorded parameters and revision provenance.",
            "Total variation distance measures distribution difference, not individual sampled outcomes switching answers.",
        ],
    }
    runs = value["payload"]["runs"]
    if not runs and run_index == pass_index == 0 and token_position is None and offset == 0:
        result["limitations"].append("This saved bundle contains no sampling runs.")
        return evidence._bounded(result)
    if run_index >= len(runs):
        raise ValueError("run_index is outside saved runs.")
    run = runs[run_index]
    if pass_index >= len(run["passes"]):
        raise ValueError("pass_index is outside saved passes.")
    chosen = run["passes"][pass_index]
    root = "/payload/runs/" + str(run_index)
    record_pointer = evidence._pointer(root + "/records", chosen["id"])
    record = run["records"][chosen["id"]]
    base = run["base"]
    result["selection"].update(run_id=run["id"], response_id=run.get("source_response_id"), pass_id=chosen["id"])
    prompt_key = "prompt" if "prompt" in run["base_config"] else "question"
    result["run"] = {
        "model_id": run["model"]["model_id"], "model_revision": run["model"].get("resolved_revision"),
        "prompt": _text(run["base_config"].get(prompt_key), root + "/base_config/" + prompt_key),
        "original_response": _text(base.get("text"), root + "/base/text"),
        "token_count": base["length"], "categories": run["categories"],
        "matching_rule": run.get("outcome_rule") or (base.get("question") or {}).get("matching"),
        "sampling_design": run.get("sampling_design"), "pointer": root,
        "sampling_settings_pointer": root + "/settings", "record_pointer": record_pointer,
        "upstream_commit": run.get("upstream_commit"), "upstream_pointer": root + "/upstream_commit" if run.get("upstream_commit") else None,
        "checkpoint_step": _checkpoint_step(run, chosen),
    }
    if run.get("sampling_design") != DESIGN or record.get("sampling_design") != DESIGN:
        result["status"] = "unsupported"
        result["limitations"].append("This viewer requires position_mixture_v1 observations. Legacy weighted branch estimates are not raw sampled frequencies; inspect the saved records directly.")
        return evidence._bounded(result)
    checkpoints, grouped = _checkpoint_rows(record, record_pointer, run["categories"])
    if not checkpoints:
        result["limitations"].append("This pass has no usable saved checkpoint observations.")
        return evidence._bounded(result)
    contrast_positions = [t for t, rows in grouped.items() if _contrast(rows)]
    if token_position is None:
        token_position = 16 if 16 in contrast_positions else (contrast_positions or list(grouped))[0]
    if token_position not in grouped:
        raise ValueError("token_position is not a saved checkpoint.")
    result["run"]["token_context"] = _token_context(base, root, token_position, result["run"]["checkpoint_step"])
    result["reconstruction"] = _saved_reconstruction(run, chosen, root, pass_index, checkpoints,
                                                    result["run"]["checkpoint_step"])
    visible = checkpoints[:MAX_CHECKPOINTS]
    if token_position not in {row["t"] for row in visible}:
        visible[-1] = next(row for row in checkpoints if row["t"] == token_position)
        visible.sort(key=lambda row: row["t"])
    result.update(status="ready", checkpoints=visible, checkpoints_total=len(checkpoints),
                  checkpoints_truncated=len(visible) < len(checkpoints))
    if result["checkpoints_truncated"]:
        result["limitations"].append("The checkpoint overview is capped at 128 points, including the selection. Inspect the record pointer for the complete saved grid; gaps are not interpolated.")
    result["selection"]["token_position"] = token_position
    rows = grouped[token_position]
    if offset > len(rows):
        raise ValueError("offset exceeds the selected checkpoint continuation count.")
    shared = _text("".join(base["tokens"][:token_position]), root + "/base/tokens")
    shared.update(derived_from_tokens=True, token_start=0, token_end=token_position)
    contrast = _contrast(rows)
    result["selected"] = {
        "t": token_position, "shared_prefix": shared, "contrast": contrast,
        "contrast_reason": None if contrast else "No pair of completed saved texts with distinct non-Other labels is available at this checkpoint.",
        "continuations": {"items": rows[offset:offset + limit], "offset": offset, "limit": limit,
                          "total": len(rows), "next_offset": offset + limit if offset + limit < len(rows) else None},
    }
    if any(row["text_missing"] for row in rows):
        result["limitations"].append("Some saved observations lack continuation_text. Their metadata is retained; no continuation is reconstructed or invented.")
    if any(row["label_source"] is None or row["stop_reason"] is None for row in rows):
        result["limitations"].append("Some selected observation status metadata is absent; absence does not imply completion or classification success.")
    if any(row["stop_reason_evidence"] == "inferred_from_stripped_length" for row in rows):
        result["limitations"].append("Some saved stop reasons were inferred from stripped continuation length, not directly recorded EOS tokens; this qualifier is preserved per observation.")
    if "".join(base["tokens"]) != base["text"]:
        result["limitations"].append("The shared-prefix preview concatenates saved token strings, which do not exactly concatenate to the saved original response; no tokenizer reconstruction was performed.")
    return evidence._bounded(result)
