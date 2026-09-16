# generated: Codex — fork-microscope-revamp-ASTRA-BRIEF.md, Stage C.
"""Browser classifier previews must agree with the execution classifier."""
import json
from pathlib import Path
import shutil
import subprocess
import pytest
from fork_microscope.investigation_records import classify
ROOT=Path(__file__).resolve().parents[1]


def test_browser_preview_matches_execution():
    if not shutil.which('node'):pytest.skip('Node is required for browser parity checks')
    rule=dict(schema='fork-outcome-rule-v1',method='text_match',answers=['Plan A','Plan B'])
    cases=[(rule,t,c,m) for t,c,m in [
        ('Plan A is better.',True,False),('Reject Plan A; choose Plan B.',True,False),
        ('plan ABC',True,False),('Ｐｌａｎ　Ａ',True,False),('Plan\x85A',True,False),
        ('Plan A',False,False),('<think>Plan A',True,False),
        ('<think>Plan A</think>Plan B',True,False),
        ('reasoning Plan A to=user<|message|>Plan B<|eot|>',True,True),
        ('Plan B',True,True)]]
    final=dict(rule,method='final_marker',marker='Final answer:')
    cases += [(final,t,True,False) for t in ['Plan A looks bad.\nFinal answer: Plan B','Final answer: Plan A\nFinal answer: Plan B','My Final answer: Plan A','Final answer:\nPlan A','Final answer: Plan A\n\n']]
    cases += [(dict(rule,answers=['straße','Other route']), 'STRASSE',True,False),(dict(rule,answers=['Σ','Ｂ']), 'ς',True,False)]
    script="""import fs from 'node:fs';import {classifyPreview} from './public/fork-microscope/classification.mjs';const cases=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(cases.map(c=>classifyPreview(...c))));"""
    got=json.loads(subprocess.check_output(['node','--input-type=module','-e',script],input=json.dumps(cases).encode(),cwd=ROOT))
    expected=[]
    for r,t,c,m in cases:
        value=classify(r,t,c,m);value.pop('rule_id');expected.append(value)
    assert got==expected


def test_browser_fixture_satisfies_python_bundle_validator():
    from fork_microscope.investigation_bundle import validate_bundle
    value=json.loads((ROOT/'tests/fixtures/investigation-v3.json').read_text())
    validate_bundle(value)
