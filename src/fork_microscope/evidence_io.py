"""Import completed evidence exports without executing code or replacing saved runs."""
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
import time

MAX_IMPORT_BYTES = 64 * 1024 * 1024
RUN_ID = re.compile(r"[0-9a-f]{32}\Z")
PASS_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def validate_export(value):
    """Validate structure and recorded histograms; this is not scientific certification."""
    if not isinstance(value, dict) or not RUN_ID.fullmatch(str(value.get("id", ""))):
        raise ValueError("Choose a completed Fork Microscope evidence export with a valid run ID.")
    if value.get("schema_version") not in (1, 2):
        raise ValueError("This evidence version is not supported. Import a version 1 or 2 export.")
    required = ("model", "base", "base_config", "settings", "records", "passes", "categories")
    if any(key not in value for key in required):
        raise ValueError("This file is missing raw evidence. Use Export evidence from the source run.")
    if not isinstance(value["model"], dict) or not isinstance(value["model"].get("model_id"), str):
        raise ValueError("The export has no model identity.")
    if not isinstance(value.get("created"), (int, float)) or not math.isfinite(value["created"]):
        raise ValueError("The export has an invalid creation time.")
    categories = value["categories"]
    if (not isinstance(categories, list) or not 2 <= len(categories) <= 33
            or any(not isinstance(x, str) or not x or len(x) > 200 for x in categories)
            or len(set(categories)) != len(categories)):
        raise ValueError("The export has invalid outcome labels.")
    base = value["base"]
    if not isinstance(base, dict) or not isinstance(base.get("text"), str) or not isinstance(base.get("tokens"), list):
        raise ValueError("The export is missing its original trace.")
    length = base.get("length")
    if (type(length) is not int or length < 1 or len(base["tokens"]) != length
            or any(not isinstance(token, str) for token in base['tokens'])):
        raise ValueError("Original token count does not match the saved trace.")
    from fork_microscope.sampling import pass_plan
    pass_plan(value['settings'], length - 1)
    config = value['base_config']
    if not isinstance(config, dict) or config.get('mode') not in ('chat', 'base'):
        raise ValueError('The export has invalid prompt settings.')
    if type(config.get('max_tokens')) is not int or config['max_tokens'] < 1:
        raise ValueError('The original generation limit is missing.')
    if type(config.get('seed')) is not int or not 0 <= config['seed'] <= 2**31 - 1:
        raise ValueError('The original generation seed is missing.')
    if 'prompt' in config:
        from fork_microscope.outcome_readout import validate_answers
        if not isinstance(config['prompt'], str) or not config['prompt'].strip():
            raise ValueError('The original prompt is missing.')
        if validate_answers(config.get('answers')) + ['Other'] != categories:
            raise ValueError('The original answer list does not match the saved outcome labels.')
    elif (not isinstance(config.get('question'), str) or not isinstance(config.get('choices'), list)
            or len(config['choices']) != 4 or any(not isinstance(x, str) for x in config['choices'])):
        raise ValueError('The original question and choices are missing.')
    passes, records = value["passes"], value["records"]
    if not isinstance(passes, list) or not 1 <= len(passes) <= 16 or not isinstance(records, dict):
        raise ValueError("The export has invalid sampling passes.")
    ids = [p.get("id") for p in passes if isinstance(p, dict)]
    if (len(ids) != len(passes) or len(set(ids)) != len(ids)
            or any(not isinstance(x, str) or not PASS_ID.fullmatch(x)
                   or x in {"manifest", "result", "dense", "replay-verification", "import-info"} for x in ids)):
        raise ValueError("The export has invalid or duplicate pass IDs.")
    allowed = set(ids) | {"dense", "replay-verification", "import-info"}
    if not set(records) <= allowed or not set(ids) <= set(records):
        raise ValueError("The export contains missing or unexpected raw record files.")
    source_ids = None
    for p in passes:
        record = records[p["id"]]
        if not isinstance(record, dict) or record.get("categories") != categories:
            raise ValueError("Raw evidence and graph outcome labels do not match.")
        raw_base = record.get("base", {})
        token_ids = raw_base.get("gen_ids", [])
        prompt_ids = raw_base.get('prompt_ids')
        if (len(token_ids) != length or any(type(x) is not int or x < 0 for x in token_ids)
                or not isinstance(prompt_ids, list) or not prompt_ids
                or any(type(x) is not int or x < 0 for x in prompt_ids)
                or raw_base.get("base_text") != base["text"]):
            raise ValueError("Raw evidence and graph do not share the same original trace.")
        if source_ids is None:
            source_ids = (prompt_ids, token_ids)
        elif source_ids != (prompt_ids, token_ids):
            raise ValueError('Passes do not share the same original token IDs.')
        curve = p.get("curve", {})
        positions, rows = curve.get("positions", []), curve.get("weighted", [])
        if (not positions or len(positions) != len(rows)
                or any(type(t) is not int or not 0 <= t < length for t in positions)
                or positions != sorted(set(positions))):
            raise ValueError("The export has invalid checkpoint positions.")
        for row in rows:
            if (not isinstance(row, list) or len(row) != len(categories)
                    or any(not isinstance(x, (int, float)) or not math.isfinite(x) or not 0 <= x <= 1 for x in row)
                    or abs(sum(row) - 1) > 1e-6):
                raise ValueError("Outcome proportions must form a valid probability distribution.")
        support, smoothed = curve.get('support', []), curve.get('smoothed', [])
        if (not isinstance(support, list) or not isinstance(smoothed, list)
                or len(support) != len(smoothed)
                or any(type(t) is not int or not positions[0] <= t <= positions[-1] for t in support)
                or support != sorted(set(support))):
            raise ValueError('The saved fitted curve has invalid token coordinates.')
        for row in smoothed:
            if (not isinstance(row, list) or len(row) != len(categories)
                    or any(not isinstance(x, (int, float)) or not math.isfinite(x) or not 0 <= x <= 1 for x in row)
                    or abs(sum(row) - 1) > 1e-6):
                raise ValueError('The saved fitted curve has invalid probabilities.')
        adjacent = set(zip(positions, positions[1:]))
        for boundary in curve.get('boundaries', []):
            if not isinstance(boundary, dict) or (boundary.get('left'), boundary.get('right')) not in adjacent:
                raise ValueError('Candidate boundaries must lie between adjacent measured checkpoints.')
        if record.get("sampling_design") == "position_mixture_v1":
            from fork_microscope.sampling import position_draws
            import numpy as np
            for branch in record['branches']:
                labels, observations = branch['answers'], branch.get('observations', [])
                if (len(observations) != len(labels)
                        or any(not isinstance(o, dict) or o.get('label') != label
                               for label, o in zip(labels, observations))
                        or len(branch.get('continuation_ids', [])) != len(labels)):
                    raise ValueError('Displayed continuations and recorded outcome counts do not match.')
            actual_positions, draws = position_draws(record)
            if actual_positions != positions:
                raise ValueError("Recorded checkpoint positions do not match the graph.")
            for row, samples in zip(rows, draws):
                histogram = np.bincount(samples, minlength=len(categories)) / len(samples)
                if not np.allclose(row, histogram, atol=1e-8, rtol=0):
                    raise ValueError("The graph does not match its raw recorded outcomes.")
    return value


def import_export(value, runs):
    try:
        validate_export(value)
        # Reject nonfinite nested data before writing any files.
        json.dumps(value, allow_nan=False)
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError("This file has malformed evidence. Export the completed run again.") from exc
    runs = Path(runs)
    target = runs / value["id"]
    result = {key: val for key, val in value.items() if key != "records"}
    if target.exists():
        try:
            identical = json.loads((target / "result.json").read_text()) == result and all(
                json.loads((target / (name + ".json")).read_text()) == data
                for name, data in value["records"].items())
        except (OSError, ValueError):
            identical = False
        if not identical:
            raise ValueError("A different run with this ID already exists. Existing evidence was not replaced.")
        return {"id": value["id"], "already_present": True}
    runs.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".import-", dir=runs))
    try:
        manifest = {key: val for key, val in result.items()
                    if key not in ("base", "categories", "passes", "reference", "measured", "caveats")}
        files = {"manifest": manifest, "result": result, **value["records"]}
        files['import-info'] = {'imported_at': time.time(), 'fit_recomputed': False,
                                'note': 'Imported evidence; stored fitted curves were not recomputed locally.'}
        for name, data in files.items():
            (staging / (name + ".json")).write_text(json.dumps(data, allow_nan=False), encoding="utf-8")
        staging.rename(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {"id": value["id"], "already_present": False}
