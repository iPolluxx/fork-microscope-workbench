"""Offline source-fidelity and bounded-state checks for the saved graph view."""
import copy
from collections import Counter
import hashlib
import importlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "integrations/mcp/src"))
from offline_support import adapter_context
from fork_microscope.investigation_bundle import build_bundle, build_bundle_v3


@pytest.fixture
def saved(tmp_path, monkeypatch):
    source = ROOT / "public/fork-microscope/demo-portfolio.json"
    raw = source.read_bytes()
    root = tmp_path / "evidence"
    root.mkdir()
    (root / "saved.json").write_bytes(raw)
    with adapter_context():
        module = importlib.import_module("tools.viewer_data")
        current_evidence = importlib.import_module("tools.evidence")
        monkeypatch.setattr(module, "evidence", current_evidence)
        monkeypatch.setattr(current_evidence, "load_profile", lambda: {"evidence_dir": root})
        yield module, root, raw


def rewrite_run(saved, edit):
    module, root, raw = saved
    run = copy.deepcopy(json.loads(raw)["payload"]["runs"][0])
    edit(run)
    value = build_bundle([run])
    (root / "saved.json").write_text(json.dumps(value))
    return value


def test_public_default_counts_provenance_and_selection(saved):
    module, root, raw = saved
    result = module.get_investigation_view("saved.json")
    assert result["schema"] == "fork-microscope-view-v1"
    assert result["status"] == "ready"
    assert result["selection"]["token_position"] == 16
    assert result["bundle"]["file_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["run"]["model_id"] == "Qwen/Qwen2.5-1.5B-Instruct"
    assert result["run"]["model_revision"] == "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"
    assert [list(point["counts"].values()) for point in result["checkpoints"]] == [
        [16, 2, 2], [13, 6, 1], [17, 1, 2], [18, 2, 0], [17, 0, 3], [15, 0, 5]]
    assert [point["n"] for point in result["checkpoints"]] == [20] * 6
    assert result["checkpoints"][1]["retained_mass"] == 0.6055173451989238
    assert result["checkpoints"][1]["candidates"] == 5
    assert result["readouts"] == {"lens_count": 0, "capture_count": 0, "patch_count": 0, "scope": "bundle"}
    assert [row["draw_index"] for row in result["selected"]["contrast"]] == [0, 3]
    assert [row["label"] for row in result["selected"]["contrast"]] == ["CHOICE=A", "CHOICE=B"]
    source = json.loads(raw)
    for key in ("prompt", "original_response"):
        field = result["run"][key]
        assert module.evidence._select(source, field["pointer"]) == field["text"]
    prefix = result["selected"]["shared_prefix"]
    assert prefix["text"] == "".join(module.evidence._select(source, prefix["pointer"])[:16])
    assert prefix["derived_from_tokens"] is True
    assert len(json.dumps(result, ensure_ascii=True).encode()) <= 128 * 1024


def test_public_tokens_and_saved_reconstruction_match_original_pointers(saved):
    module, _, raw = saved
    source = json.loads(raw)
    result = module.get_investigation_view("saved.json")
    assert result["bundle"]["file_sha256"] == "efd568b4c21ab4b37e2dde85299744f36e940a6b32770ef84e1600fe5308b61d"
    assert result["run"]["checkpoint_step"] == 16
    assert result["run"]["upstream_commit"] == "d32fed8d4162a4888291c4b3a38b059727c85a41"
    assert module.evidence._select(source, result["run"]["upstream_pointer"]) == result["run"]["upstream_commit"]
    context = result["run"]["token_context"]
    assert [context[key] for key in ("token_start", "token_end", "total_tokens", "truncated")] == [0, 85, 85, False]
    assert [item["text"] for item in context["items"]] == source["payload"]["runs"][0]["base"]["tokens"]
    for item in context["items"]:
        assert module.evidence._select(source, item["pointer"]) == item["text"]
        assert not item["text_truncated"]
    reconstruction = result["reconstruction"]
    assert reconstruction["available"] and reconstruction["reason"] is None
    assert [point["t"] for point in reconstruction["points"]] == list(range(81))
    for point in reconstruction["points"]:
        assert module.evidence._select(source, point["pointer"]) == point["probabilities"]
        assert module.evidence._select(source, point["coordinate_pointer"]) == point["t"]
        for bound in ("low", "high"):
            assert module.evidence._select(source, point["bands_pointer"][bound]) == point[bound]
    provenance = reconstruction["provenance"]
    assert provenance["parameters"] == {"h": 8.0, "pen": 1.0, "variant": "linear"}
    assert provenance["tuning"] == "cv" and provenance["otrecon_version"] == "1.0.0"
    assert provenance["method_recorded"] is False and "not a recorded" in provenance["method_basis"]
    assert "may under-cover" in " ".join(reconstruction["limitations"])
    assert "distribution difference" in " ".join(result["limitations"])
    assert len(json.dumps(result, ensure_ascii=True).encode()) <= 128 * 1024


def test_checkpoint_adjacency_and_denominator_pointers_are_source_grounded(saved):
    module, _, raw = saved
    source = json.loads(raw)
    result = module.get_investigation_view("saved.json")
    for index, point in enumerate(result["checkpoints"]):
        assert point["previous_observed_t"] == (index - 1) * 16 if index else point["previous_observed_t"] is None
        assert point["next_observed_t"] == (index + 1) * 16 if index < 5 else point["next_observed_t"] is None
        assert module.evidence._select(source, point["n_pointer"]) == point["n"]
        assert isinstance(module.evidence._select(source, point["counts_source_pointer"]), list)


@pytest.mark.parametrize("edit,reason", [
    (lambda r: r["passes"][0]["curve"].update(fit_status="withheld"), "withheld"),
    (lambda r: r["passes"][0]["curve"].pop("parameters"), "parameters"),
    (lambda r: r.pop("upstream_commit"), "provenance"),
    (lambda r: r["versions"].pop("otrecon"), "provenance"),
])
def test_missing_or_withheld_fit_preserves_raw_evidence_without_inventing_fit(saved, edit, reason):
    rewrite_run(saved, edit)
    result = saved[0].get_investigation_view("saved.json")
    assert result["status"] == "ready" and len(result["checkpoints"]) == 6
    assert not result["reconstruction"]["available"] and result["reconstruction"]["points"] == []
    assert reason in result["reconstruction"]["reason"]


def test_sparse_saved_fit_never_fills_missing_token_positions(saved):
    def sparse(run):
        curve = run["passes"][0]["curve"]
        for key in ("support", "smoothed", "low", "high"):
            curve[key] = curve[key][::16]
    rewrite_run(saved, sparse)
    result = saved[0].get_investigation_view("saved.json")
    assert result["status"] == "ready" and len(result["checkpoints"]) == 6
    assert not result["reconstruction"]["available"] and result["reconstruction"]["points"] == []
    assert "dense per-token" in result["reconstruction"]["reason"]


def test_fractional_recorded_horizon_rejected_but_large_integer_stride_keeps_counts(saved):
    rewrite_run(saved, lambda run: run["passes"][0]["configuration"].update(stride=1.5))
    with pytest.raises(ValueError, match="positive integer"):
        saved[0].get_investigation_view("saved.json")
    rewrite_run(saved, lambda run: run["passes"][0]["configuration"].update(stride=100))
    result = saved[0].get_investigation_view("saved.json")
    assert result["status"] == "ready" and len(result["checkpoints"]) == 6
    assert result["run"]["checkpoint_step"] == 100
    assert not result["reconstruction"]["available"]
    assert "no exact" in result["reconstruction"]["reason"]


def test_long_saved_token_strings_are_marked_and_keep_exact_retrieval_pointer(saved):
    module, _, _ = saved
    tokens = ["🙂</script>" * 30 for _ in range(1000)]
    context = module._token_context({"tokens": tokens, "text": "".join(tokens)}, "/run", 800, 16)
    assert context["token_start"] == 766 and context["token_end"] == 1000
    assert context["total_tokens"] == 1000 and context["window_truncated"] and context["text_truncated"]
    assert context["truncated"] and context["items"][0]["text_truncated"]
    assert context["items"][0]["text"] == tokens[766][:module.TOKEN_TEXT_LIMIT]
    assert context["items"][0]["pointer"] == "/run/base/tokens/766"


def test_all_120_paginated_rows_match_exact_source(saved):
    module, root, raw = saved
    source, sha = json.loads(raw), hashlib.sha256(raw).hexdigest()
    observations = []
    for t in (0, 16, 32, 48, 64, 80):
        offset, rows = 0, []
        while offset is not None:
            result = module.get_investigation_view("saved.json", token_position=t, offset=offset,
                                                   limit=5, expected_file_sha256=sha)
            page = result["selected"]["continuations"]
            rows.extend(page["items"])
            offset = page["next_offset"]
        assert [row["draw_index"] for row in rows] == list(range(20))
        for row in rows:
            original = module.evidence._select(source, row["pointer"])
            original_text = module.evidence._select(source, row["text_pointer"])
            assert row["text"] == original_text
            assert not row["text_truncated"] and not row["text_missing"]
            assert hashlib.sha256(row["text"].encode()).digest() == hashlib.sha256(original_text.encode()).digest()
            for key in ("label", "label_source", "stop_reason", "stop_reason_evidence", "generated_tokens"):
                assert row[key] == original[key]
            assert row["classification_status"] == original.get("classification_status")
            observations.append(row)
    assert len(observations) == 120
    others = [row for row in observations if row["label"] == "Other"]
    assert len(others) == 13
    assert Counter((row["stop_reason"], row["label_source"]) for row in others) == {
        ("eos", "unparsed"): 11, ("eos", "ambiguous"): 1, ("length", "incomplete"): 1}


def test_no_substitution_for_absent_contrast(saved):
    module, _, _ = saved
    for t in (64, 80):
        result = module.get_investigation_view("saved.json", token_position=t)
        assert result["selected"]["contrast"] == []
        assert "No pair" in result["selected"]["contrast_reason"]


def test_synthetic_generic_outcomes_and_first_contrast_default(saved):
    module, root, _ = saved
    categories = ["Accept", "Defer", "Other"]
    answers = ["Accept", "Accept", "Other", "Defer", "Accept"]
    settings = dict(passes=[dict(id="scan", label="Scan", start=0, end=1, stride=1,
                                offset=0, samples=5, seed=0)], cont_max=8,
                    temperature=1, top_k=1, threshold=.05, dense=False,
                    reference_samples=5, tuning="cv")
    positions, branches = [], []
    for t in (0, 1):
        positions.append(dict(t=t, samples=5, retained_mass=1,
                              candidates=[dict(tok_id=2 + t, tok_p=1, is_base=True)]))
        observations = [dict(label=label, label_source="incomplete" if label == "Other" else "answer_text",
                             stop_reason="length" if label == "Other" else "eos", generated_tokens=1,
                             continuation_text=f"synthetic continuation {t}:{i}") for i, label in enumerate(answers)]
        branches.append(dict(t=t, tok_id=2 + t, is_base=True, answers=answers[:],
                             draw_indices=list(range(5)), observations=observations,
                             continuation_ids=[[4] for _ in answers]))
    record = dict(sampling_design="position_mixture_v1", categories=categories,
                  base=dict(gen_ids=[2, 3], prompt_ids=[1], base_text="ab"),
                  positions=positions, branches=branches)
    run = dict(id="a" * 32, schema_version=2, created=1, sampling_design="position_mixture_v1",
               model={"model_id": "synthetic/test"}, categories=categories,
               base={"length": 2, "text": "ab", "tokens": ["a", "b"]},
               base_config={"prompt": "Synthetic question", "answers": categories[:-1],
                            "mode": "chat", "max_tokens": 2, "seed": 0}, settings=settings,
               records={"scan": record}, passes=[dict(id="scan", curve={"positions": [0, 1],
                                                                       "weighted": [[.6, .2, .2]] * 2})])
    (root / "saved.json").write_text(json.dumps(build_bundle([run])))
    result = module.get_investigation_view("saved.json")
    assert result["run"]["categories"] == categories
    assert result["selection"]["token_position"] == 0
    assert result["checkpoints"][0]["counts"] == {"Accept": 3, "Defer": 1, "Other": 1}
    assert [row["label"] for row in result["selected"]["contrast"]] == ["Accept", "Defer"]
    assert result["run"]["model_revision"] is None


def test_hash_pin_and_missing_page_pin(saved):
    module, root, raw = saved
    sha = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match="require expected"):
        module.get_investigation_view("saved.json", offset=5)
    (root / "saved.json").write_bytes(raw + b"\n")
    with pytest.raises(ValueError, match="changed"):
        module.get_investigation_view("saved.json", expected_file_sha256=sha)


@pytest.mark.parametrize("kwargs", [
    {"offset": -1}, {"offset": True}, {"limit": 0}, {"limit": 21}, {"limit": True},
    {"run_index": True}, {"run_index": 1}, {"pass_index": -1}, {"pass_index": 1},
    {"token_position": True}, {"token_position": 3}, {"expected_file_sha256": None},
])
def test_input_bounds(saved, kwargs):
    with pytest.raises(ValueError):
        saved[0].get_investigation_view("saved.json", **kwargs)


def test_empty_bundle_does_not_invent_sampling_evidence(saved):
    module, root, _ = saved
    job = dict(id="b" * 32, schema="fork-workflow-v2", context={"provenance": "legacy_inferred"},
               runs=[], lenses=[], patches=[], responses=[], edits=[], captures=[], selected_response_id=None)
    (root / "saved.json").write_text(json.dumps(build_bundle_v3([], investigation=job)))
    result = module.get_investigation_view("saved.json")
    assert result["status"] == "empty"
    assert result["run"] is None and result["selected"] is None
    assert result["checkpoints"] == []


def test_legacy_weighted_record_not_presented_as_counts(saved):
    def legacy(run):
        run["sampling_design"] = "legacy_weighted"
        for record in run["records"].values():
            record["sampling_design"] = "legacy_weighted"
            for branch in record["branches"]:
                branch.pop("observations", None)
    rewrite_run(saved, legacy)
    result = saved[0].get_investigation_view("saved.json")
    assert result["status"] == "unsupported"
    assert result["checkpoints"] == [] and result["selected"] is None
    assert "Legacy weighted" in " ".join(result["limitations"])


def test_missing_continuation_and_metadata_are_not_invented(saved):
    def missing(run):
        for record in run["records"].values():
            for branch in record["branches"]:
                for observation in branch["observations"]:
                    observation.pop("continuation_text", None)
                    observation.pop("label_source", None)
    rewrite_run(saved, missing)
    result = saved[0].get_investigation_view("saved.json", token_position=16)
    assert result["selected"]["contrast"] == []
    assert all(row["text"] is None and row["text_missing"] and row["label_source"] is None
               for row in result["selected"]["continuations"]["items"])
    assert result["checkpoints"][1]["counts"] == {"CHOICE=A": 13, "CHOICE=B": 6, "Other": 1}


def test_bounded_text_has_exact_retrieval_pointer(saved):
    text = "A🙂</script>" * 3000
    def long_text(run):
        for record in run["records"].values():
            for branch in record["branches"]:
                for observation in branch["observations"]:
                    observation["continuation_text"] = text
    source = rewrite_run(saved, long_text)
    result = saved[0].get_investigation_view("saved.json", limit=1)
    row = result["selected"]["continuations"]["items"][0]
    assert row["text"] == text[:saved[0].TEXT_LIMIT] and row["text_truncated"]
    assert saved[0].evidence._select(source, row["text_pointer"]) == text
    assert result["inspect"]["expected_file_sha256"] == result["bundle"]["file_sha256"]


def test_oversize_untrusted_metadata_is_rejected(saved):
    rewrite_run(saved, lambda run: run["model"].update(model_id="x" * (128 * 1024)))
    with pytest.raises(ValueError, match="128 KiB"):
        saved[0].get_investigation_view("saved.json")


def test_missing_retained_metadata_is_unknown_not_zero(saved):
    def missing(run):
        for record in run["records"].values():
            for point in record["positions"]:
                point.pop("retained_mass", None)
                point.pop("candidates", None)
    rewrite_run(saved, missing)
    result = saved[0].get_investigation_view("saved.json")
    assert all(point["retained_mass"] is None and point["candidates"] is None for point in result["checkpoints"])


def test_checkpoint_overview_cap_preserves_requested_selection(saved, monkeypatch):
    monkeypatch.setattr(saved[0], "MAX_CHECKPOINTS", 2)
    result = saved[0].get_investigation_view("saved.json", token_position=80)
    assert result["checkpoints_total"] == 6 and result["checkpoints_truncated"]
    assert [point["t"] for point in result["checkpoints"]] == [0, 80]
    assert result["selected"]["t"] == 80


@pytest.mark.parametrize("bundle_id", ["../outside.json", "/tmp/outside.json", "x\\outside.json"])
def test_profile_path_boundary_reused(saved, bundle_id):
    with pytest.raises(ValueError):
        saved[0].get_investigation_view(bundle_id)
