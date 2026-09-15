import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from fork_microscope.refinement import build_plan
from fork_microscope.live_service import LiveService

def fixture():
    config=json.loads((Path(__file__).parents[1]/'tests/fixtures/refinement.json').read_text())
    result=dict(id='a'*32,passes=[{'id':'pass_1'}],settings=config['run'],model={'model_id':'tiny','resolved_revision':'pinned'},base_config=config['base'])
    record=dict(base={'prompt_ids':[1,2],'gen_ids':[3]*500,'base_text':'saved','finish_reason':'stop'},positions=[])
    request=dict(source_run_id='a'*32,source_pass_id='pass_1',start=339,end=452,stride=8,samples=20,seed=23,cont_max=2048)
    return result,record,request

def test_plan_exact_endpoints_and_no_source_mutation():
    r,b,q=fixture();before=json.dumps(r);plan=build_plan(r,b,q)
    assert plan['summary']['continuations']==320
    assert plan['run']['passes'][0]['positions']==list(range(339,453,8))+[452]
    assert not plan['run']['dense'] and json.dumps(r)==before
    assert plan['lineage']['selected_after_inspecting_source']
    q['end']=600
    with pytest.raises(ValueError):build_plan(r,b,q)

def test_wrong_model_refinement_rejected_before_job(monkeypatch):
    r,b,q=fixture();s=LiveService();monkeypatch.setattr(s,'refinement_plan',lambda p:build_plan(r,b,p))
    s.model=SimpleNamespace(info={'model_id':'tiny','resolved_revision':'wrong'})
    with pytest.raises(ValueError,match='exact'):s.start('refine',q)
    assert s.job['status']=='idle'

def test_refinement_uses_replay_and_records_lineage(monkeypatch):
    from fork_microscope import replay_trace
    r,b,q=fixture();plan=build_plan(r,b,q);s=LiveService();s.model=object();base=SimpleNamespace(gen_ids=b['base']['gen_ids'])
    monkeypatch.setattr(replay_trace,'replay_saved_trace',lambda *a,**k:base)
    collected=[];monkeypatch.setattr(s,'collect',lambda config:collected.append((config,s.base,s.lineage)))
    s.job={'status':'running','total':320};s._execute('refine',plan)
    assert s.job['status']=='complete' and collected[0][1] is base
    assert collected[0][2]['source_run_id']==r['id'] and collected[0][2]['exact_ids_verified']
