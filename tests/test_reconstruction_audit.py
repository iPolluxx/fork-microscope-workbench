# generated: Codex — Paper2Agent-guided reconstruction audit, 2026-09-17.
"""Public-data reconstruction parity and CLI failure checks; no model generation."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/verify_math.py'

@pytest.mark.parametrize('args', [['--case', 'unknown:12'], ['--seed', '-1'], ['--checkpoints', '3'], ['--samples', '21']])
def test_audit_rejects_unsupported_inputs(args, tmp_path):
    output = tmp_path / 'result.json'
    result = subprocess.run([sys.executable, str(SCRIPT), '--output', str(output), *args],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 2
    assert not output.exists()

@pytest.mark.parametrize('samples,seed', [(20, 43000000), (40, 43000001)])
def test_selected_public_case_matches_pinned_reference(samples, seed, tmp_path):
    output = tmp_path / 'result.json'
    subprocess.run([sys.executable, str(SCRIPT), '--case', 'llama:12', '--checkpoints', '6',
                    '--samples', str(samples), '--seed', str(seed), '--output', str(output)],
                   check=True, capture_output=True, text=True, timeout=120)
    report = json.loads(output.read_text())
    assert report['passed'] and report['scope'] == 'selected_public_stores'
    assert len(report['cases']) == 1
    assert all(m['matches_pinned'] for m in report['runtime_sources'].values())
    case = report['cases'][0]
    assert len(case['positions']) == 6
    assert all(len(row) == samples for row in case['draws'])
    for key in ['raw','parameters','best_cv_score','support','smoothed','low','high','boundaries']:
        assert case['application'][key] == case['upstream'][key]
    assert all(sum(row) == samples for row in case['counts'])
    assert case['mixture_diagnostics']['exhausted_fallbacks'] == 0

def test_audit_rejects_mismatched_loaded_estimator(tmp_path):
    import os
    import shutil
    package = tmp_path / 'otrecon'
    shutil.copytree(ROOT / 'vendor/forking-fast/otrecon/otrecon', package)
    with (package / 'data.py').open('a') as stream:
        stream.write('\n# Simulated drift in the installed estimator.\n')
    output = tmp_path / 'result.json'
    result = subprocess.run([sys.executable, str(SCRIPT), '--case', 'llama:12', '--output', str(output)],
                            env={**os.environ, 'PYTHONPATH': str(tmp_path)},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert 'differs from the pinned upstream source' in result.stderr
    assert not output.exists()
