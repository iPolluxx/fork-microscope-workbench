"""Local recorded-data service for the two-pass fork microscope. No generation."""
from __future__ import annotations

from functools import lru_cache
import importlib.util
import json
from pathlib import Path

import numpy as np
from otrecon import data as od
from otrecon.models import MODEL_REGISTRY

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / "vendor" / "forking-fast"
spec = importlib.util.spec_from_file_location("microscope_loader", REPO / "data/loader.py")
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)
COMMIT = "d32fed8d4162a4888291c4b3a38b059727c85a41"


@lru_cache(maxsize=8)
def dataset(row):
    if type(row) is not int or not 0 <= row <= 99:
        raise ValueError("Question must be between 0 and 99.")
    rec = loader.load_store(str(REPO / f"data/s200/llama/row{row:03d}.json.gz"))
    positions, reference = od.weighted_o_t(rec)
    if positions != list(range(len(positions))):
        raise ValueError("This replay requires observations at every response token.")
    groups = od.branch_groups(rec)
    lengths = np.array([sum(w * np.mean(b["cont_lens"])
        for b, w in zip(groups[t], od.normalized_weights(groups[t]))) for t in positions])
    _, draws, diag = od.mixture_draws(rec, np.arange(5), 5, n_total=200, seed_base=43_000_000)
    if diag["exhausted_fallbacks"]:
        raise ValueError("Insufficient recorded branch draws for this replay.")
    return {"record": rec, "reference": reference, "lengths": lengths, "draws": draws}


@lru_cache(maxsize=1)
def catalog():
    items = []
    for row in range(100):
        rec = loader.load_store(str(REPO / f"data/s200/llama/row{row:03d}.json.gz"))
        items.append({"row": row, "question": rec["meta"]["question"]})
    return items


def metadata(row):
    d = dataset(row)
    rec = d["record"]
    return {"row": row, "question": rec["meta"]["question"],
        "choices": rec["meta"]["choices"], "answer": rec["meta"]["answer_letter"],
        "model": rec["meta"]["model"], "last_token": len(d["reference"])-1,
        "expected_lengths": d["lengths"].tolist(), "categories": rec["categories"],
        "tokens": rec["base"]["token_texts"], "base_text": rec["base"]["base_text"],
        "available_draws": 200, "source_commit": COMMIT}


def plan(config, last_token):
    required = {"row", "samples", "stride", "shift", "start", "end", "draw_start"}
    if set(config) != required or any(type(v) is not int for v in config.values()):
        raise ValueError("Sampling settings must be whole numbers with the expected fields.")
    if not 0 <= config["row"] <= 99:
        raise ValueError("Question must be between 0 and 99.")
    if not 1 <= config["samples"] <= 200 or not 0 <= config["draw_start"] <= 199 or config["samples"] + config["draw_start"] > 200:
        raise ValueError("Samples plus draw start must fit within the 200 recorded draws.")
    if not 2 <= config["stride"] <= 64:
        raise ValueError("Checkpoint spacing must be 2 to 64 tokens.")
    if not 1 <= config["shift"] < config["stride"]:
        raise ValueError("The second-pass shift must be smaller than the checkpoint spacing.")
    if not 0 <= config["start"] < config["end"] <= last_token:
        raise ValueError(f"Choose a region between token 0 and {last_token}.")
    first = np.arange(config["start"], config["end"] + 1, config["stride"], dtype=int)
    first = first[first + config["shift"] <= config["end"]]
    if len(first) < 2:
        raise ValueError("Widen the region to fit at least two checkpoints in each pass.")
    return first, first + config["shift"], np.arange(config["start"], config["end"]+1)


def cost(config, lengths, grids):
    first, second, dense = grids
    s = config["samples"]
    amounts = {name: float(s*np.asarray(lengths)[p].sum())
               for name, p in [("first", first), ("second", second), ("dense", dense)]}
    amounts["combined"] = amounts["first"] + amounts["second"]
    amounts["saving_fraction"] = 1-amounts["combined"]/amounts["dense"] if amounts["dense"] else None
    return {"expected_tokens": amounts, "checkpoints_per_pass": len(first),
            "combined_checkpoints": len(first)+len(second), "dense_checkpoints": len(dense),
            "combined_continuations": s*(len(first)+len(second)), "dense_continuations": s*len(dense)}


@lru_cache(maxsize=64)
def analyze_cached(row, samples, stride, shift, start, end, draw_start):
    config = dict(row=row, samples=samples, stride=stride, shift=shift, start=start, end=end, draw_start=draw_start)
    d = dataset(row)
    selected = plan(config, len(d["reference"])-1)
    curves = []
    for positions in selected[:2]:
        counts = od.counts_from_draws(d["draws"][positions], 5, draw_start, draw_start+samples)
        model = MODEL_REGISTRY["M5a_segkernel"]()
        model.fit(positions.astype(float), counts, samples, {"variant": "mult", "pen": 64.0, "h": 32.0})
        support = np.arange(positions[0], positions[-1]+1)
        predicted = model.predict(support.astype(float))
        if not np.isfinite(predicted).all() or not np.allclose(predicted.sum(axis=1), 1):
            raise RuntimeError("Reconstruction did not return valid probabilities.")
        curves.append({"positions": positions.tolist(), "raw": (counts/samples).tolist(),
                       "support": support.tolist(), "smoothed": predicted.tolist()})
    dense = selected[2]
    return {"config": config, "cost": cost(config, d["lengths"], selected),
            "first": curves[0], "second": curves[1],
            "reference": {"positions": dense.tolist(), "values": d["reference"][dense].tolist()},
            "method": {"estimator": "M5a_segkernel", "penalty": 64, "bandwidth": 32,
                       "categories": "all five retained", "source_commit": COMMIT}}


def analyze(config):
    # Validate shape before using values as cached function parameters.
    d = dataset(config.get("row"))
    plan(config, len(d["reference"])-1)
    return analyze_cached(**config)
