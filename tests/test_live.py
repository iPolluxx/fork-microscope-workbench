import os
os.environ.setdefault("OTRECON_FORCE_RUPTURES", "1")
from types import SimpleNamespace
import numpy as np
import pytest
from forking_paths.model import BasePath
from forking_paths.config import ForkingConfig
from forking_paths.outcomes import build_outcome_vectors
from otrecon import data as od
from fork_microscope.live_service import LiveService, grid_plan, reconstruct, compare

CONFIG = dict(samples=5,stride=4,shift=1,start=0,end=7,cont_max=16,
    temperature=1,top_k=2,threshold=.05,seed=0,dense=True,reference_samples=5,tuning="cv")


def service():
    s=LiveService()
    s.model=SimpleNamespace(info={"context_limit":1000},tokenizer=lambda *a,**kw:{"input_ids":[1,2]})
    s.base=BasePath([1,2],[10]*8,[[10,20]]*8,[[np.log(.75),np.log(.25)]]*8,"stop")
    return s


def test_actual_branches_drive_budget_and_topk():
    s=service();v=s.estimate(CONFIG)
    assert v["branches"]==dict(first=4,second=4,dense=12)
    assert v["combined_rollouts"]==20 and v["reference_rollouts"]==30
    assert v["max_continuation_tokens"]==50*16
    one=s.estimate(CONFIG|{"top_k":1,"dense":False})
    assert one["total_rollouts"]==20
    assert one["unique_checkpoints"]==4


def test_threshold_and_context_validation():
    s=service()
    assert s.estimate(CONFIG|{"threshold":.3})["branches"]["dense"]==6
    s.model.info["context_limit"]=10
    with pytest.raises(ValueError,match="context tokens"):
        s.estimate(CONFIG)


@pytest.mark.parametrize("change",[{"samples":True},{"samples":0},{"samples":4},{"threshold":float('nan')},
    {"shift":4},{"end":8},{"end":1},{"dense":"yes"},{"tuning":"invented"},{"extra":0}])
def test_invalid_config_is_rejected(change):
    with pytest.raises(ValueError): grid_plan(CONFIG|change,7)


def record(positions):
    return dict(meta={"row_id":0},categories=["A","B","C","D","Other"],branches=[
        dict(t=t,tok_id=tok,tok_p=p,is_base=(tok==10),answers=answers)
        for t in positions for tok,p,answers in [(10,.75,["A","A","A","B","B"]),(20,.25,["B"]*5)]])


def test_weighted_outcome_matches_upstream_equation_and_reconstruction():
    s=service();cfg=ForkingConfig(top_k=2,p_thresh=.05,tok_depth=8)
    branches=s.branches(cfg,[0,4]);rec=record([0,4])
    out=build_outcome_vectors(branches,[b["answers"] for b in rec["branches"]],rec["categories"])
    _,weighted=od.weighted_o_t(rec)
    np.testing.assert_allclose(weighted,out["o_t"])
    np.testing.assert_allclose(weighted[0],[.45,.55,0,0,0])
    fitted=reconstruct(rec,5,"cv",43_000_000)
    assert fitted["cv_candidates"]==0 # short-grid CV is disabled
    p=np.asarray(fitted["smoothed"])
    np.testing.assert_allclose(p.sum(axis=1),1)
    assert np.all(np.asarray(fitted["low"])<=np.asarray(fitted["high"]))
    metrics=compare(record(range(5)),fitted,5,44_000_000)
    assert 0<=metrics["mean_tv"]<=1 and metrics["mean_log_likelihood"]<=0


def test_failed_validation_cannot_start_job_or_change_model():
    s=service();before=s.model
    with pytest.raises(ValueError): s.start("run",CONFIG|{"shift":99})
    assert s.model is before and s.job["status"]=="idle"
    with pytest.raises(ValueError): s.result("../escape")


def test_incomplete_base_cannot_estimate_or_start_scan():
    s=service();s.base.finish_reason='length'
    for action in (lambda: s.estimate(CONFIG),lambda: s.start('run',CONFIG)):
        with pytest.raises(ValueError,match='before finishing'): action()
    assert s.job['status']=='idle'


def test_progress_distinguishes_replay_from_completed_draws():
    s=service()
    s.progress('Replaying saved tokens',64,106)
    assert s.job['progress_unit']=='tokens'
    s.progress('Pass 1',2,10)
    s.activity(dict(kind='generation',step=12,cap=128))
    assert (s.job['completed'],s.job['total'],s.job['progress_unit'])==(2,10,'continuations')
    assert s.job['activity']['step']==12
    s.progress('Reconstructing Pass 1…',10,10)
    assert s.job['activity'] is None
