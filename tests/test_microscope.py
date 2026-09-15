"""Sampling-budget invariants and real released-data reconstruction checks."""
import os
os.environ.setdefault("OTRECON_FORCE_RUPTURES", "1")
import json
import subprocess
from pathlib import Path
import numpy as np
import pytest
from fork_microscope import microscope as m

BASE = dict(row=39, samples=30, stride=4, shift=1, start=0, end=343, draw_start=0)


def test_two_interleaved_passes_equal_dense_when_every_position_is_covered():
    c = BASE | {"stride": 2}
    grids = m.plan(c, 343)
    assert sorted(np.concatenate(grids[:2]).tolist()) == grids[2].tolist()
    result = m.cost(c, np.arange(1, 345), grids)
    assert result["expected_tokens"]["saving_fraction"] == 0


def test_nonzero_region_and_unpaired_tail():
    c = BASE | {"start": 3, "end": 11, "stride": 4, "shift": 2}
    a, b, dense = m.plan(c, 343)
    assert a.tolist() == [3, 7]
    assert b.tolist() == [5, 9]
    assert dense.tolist() == list(range(3, 12))
    result = m.cost(c, np.ones(344), (a, b, dense))
    assert result["expected_tokens"]["combined"] == 120
    assert result["expected_tokens"]["dense"] == 270


@pytest.mark.parametrize("change", [{"samples": 0}, {"samples": 200, "draw_start": 1},
    {"shift": 4}, {"shift": 0}, {"end": 344}, {"start": 343}, {"end": 2},
    {"samples": 3.5}, {"row": True}, {"surprise": 1}])
def test_invalid_settings_rejected(change):
    with pytest.raises(ValueError):
        m.plan(BASE | change, 343)


def test_real_replay_probabilities_and_cost_scale():
    result = m.analyze(BASE)
    assert result["cost"]["combined_checkpoints"] == 172
    assert result["cost"]["expected_tokens"]["combined"] == pytest.approx(1037446.525055402)
    assert result["cost"]["expected_tokens"]["dense"] == pytest.approx(2066248.5090141457)
    for name in ("first", "second"):
        p = result[name]
        for field in ("raw", "smoothed"):
            arr = np.asarray(p[field])
            assert arr.shape[1] == 5
            assert np.all((arr >= 0) & (arr <= 1))
            np.testing.assert_allclose(arr.sum(axis=1), 1)
        assert p["support"][0] == p["positions"][0]
        assert p["support"][-1] == p["positions"][-1]
    c = BASE | {"samples": 60}
    twice = m.cost(c, m.dataset(39)["lengths"], m.plan(c, 343))
    assert twice["expected_tokens"]["combined"] == result["cost"]["expected_tokens"]["combined"] * 2
    assert twice["expected_tokens"]["saving_fraction"] == result["cost"]["expected_tokens"]["saving_fraction"]
    json.dumps(result, allow_nan=False)


def test_browser_calculator_matches_python_on_recorded_lengths():
    configs = [BASE, BASE | {"stride": 2}, BASE | {"start": 13, "end": 70, "shift": 3}]
    lengths = m.dataset(39)["lengths"].tolist()
    script = """import {plan,costs} from './public/fork-microscope/math.mjs';
    let input='';for await(const part of process.stdin)input+=part;
    const x=JSON.parse(input);console.log(JSON.stringify(x.configs.map(c=>costs(c,x.lengths,plan(c,x.lengths.length-1)))));"""
    values = json.loads(subprocess.check_output(["node", "--input-type=module", "-e", script],
        input=json.dumps(dict(configs=configs, lengths=lengths)), text=True, cwd=Path(__file__).parents[1]))
    for c, value in zip(configs, values):
        py = m.cost(c, lengths, m.plan(c, 343))["expected_tokens"]
        for field in ("first", "second", "dense", "combined"):
            assert value[field] == pytest.approx(py[field])
