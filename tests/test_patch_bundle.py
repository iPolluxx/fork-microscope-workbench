"""Simulated patch records exercise portability; these are not model results."""
import copy
import hashlib
import json
import time

import pytest

from test_evidence_io import archive
from fork_microscope.investigation_bundle import build_bundle, validate_bundle, import_bundle, digest, export_family


def exact_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@pytest.fixture
def patch(archive):
    base=archive['records']['pass_1']['base']
    req=dict(source_run_id=archive['id'],source_pass_id='pass_1',
             donor=dict(selection={'type':'original'},position=1),
             recipient=dict(selection={'type':'original'},position=2),
             layers=[0],samples=2,cont_max=2,temperature=1,seed=5,max_seconds=60,
             selection_rationale='Synthetic fixture to validate import, not evidence of a fork.')
    prefixes={}
    for name in ('donor','recipient'):
        position=req[name]['position'];response=base['gen_ids'][:position+1]
        ids=base['prompt_ids']+response
        prefixes[name]=dict(selection={'type':'original'},position=position,absolute_position=len(ids)-1,
                            prompt_ids=base['prompt_ids'],response_ids=response,input_ids=ids,
                            prefix_sha256=exact_hash(ids),token_id=ids[-1],token_text='x',prefix_text='synthetic')
    observations=[dict(arm=arm,draw=draw,seed=5+draw,continuation_ids=[7],label='A',
                       continuation_text='A',full_response_text='synthetic A',stop_reason='eos',
                       prefill_patch_count=0 if arm=='baseline' else 1)
                  for draw in range(2) for arm in ('baseline','identity','patched')]
    return dict(schema='fork-activation-patch-v1',id='b'*32,status='complete',created=1,
                model=archive['model'],request=req,source_base=base,
                source_ids_sha256=exact_hash({k:base[k] for k in ('prompt_ids','gen_ids')}),
                prefixes=prefixes,observations=observations,continuations=6,max_new_tokens=12,
                answers=archive['base_config']['answers'],
                summary=dict(counts={a:{'A':2} for a in ('baseline','identity','patched')},
                             identity_control=dict(draws_compared=2,exact_matches=2,passed=True),capped=0))


def test_patch_roundtrip_cpu_viewer_and_family_export(tmp_path,monkeypatch,archive,patch):
    from fork_microscope import live_service
    runs=tmp_path/'live-runs'
    monkeypatch.setattr(live_service,'RUNS',runs)
    bundle=build_bundle([archive],patches=[patch])
    assert bundle['schema']=='fork-investigation-bundle-v2'
    service=live_service.LiveService()
    imported=service.import_result(bundle)
    assert imported['patch_ids']==[patch['id']] and service.model is None
    assert service.investigation(patch['id'])==patch
    assert service.investigations()[0]['kind']=='patch'
    assert service.import_result(bundle)['already_present']
    exported=export_family(service,archive['id'])
    assert exported['payload']['patches']==[patch]
    assert exported['payload']['runs'][0]['records']==archive['records']
    assert service.job['status']=='idle'  # Import/export never schedules inference.


@pytest.mark.parametrize('defect',['missing_source','tokens','position','model','summary','count','duplicate','seed','limits','identity','id','private','nonfinite'])
def test_patch_malformed_rejected_before_writes(tmp_path,archive,patch,defect):
    bundle=build_bundle([archive],patches=[patch]);p=bundle['payload']['patches'][0]
    if defect=='missing_source':p['request']['source_run_id']='c'*32
    if defect=='tokens':p['prefixes']['donor']['input_ids']=[123]
    if defect=='position':p['request']['donor']['position']=900
    if defect=='model':p['model']['resolved_revision']='wrong'
    if defect=='summary':p['summary']['counts']['patched']={'B':2}
    if defect=='count':p['observations'].pop()
    if defect=='duplicate':p['observations'][-1]=copy.deepcopy(p['observations'][0])
    if defect=='seed':p['observations'][0]['seed']=99
    if defect=='limits':p['request']['samples']=1000
    if defect=='identity':
        p['request']['donor']=copy.deepcopy(p['request']['recipient'])
        p['prefixes']['donor']=copy.deepcopy(p['prefixes']['recipient'])
    if defect=='id':p['id']='../../escape'
    if defect=='private':p['api_key']='never-export-this'
    if defect=='nonfinite':p['request']['temperature']=float('nan')
    if defect!='nonfinite':bundle['sha256']=digest(bundle['payload'])
    with pytest.raises(ValueError):import_bundle(bundle,tmp_path/'live-runs')
    assert not list(tmp_path.iterdir())


def test_patch_collision_preserves_existing_file(tmp_path,archive,patch):
    bundle=build_bundle([archive],patches=[patch]);import_bundle(bundle,tmp_path/'live-runs')
    path=tmp_path/'investigations'/(patch['id']+'.json');before=path.read_bytes()
    patch['created']=3
    with pytest.raises(ValueError,match='Conflicting'):
        import_bundle(build_bundle([archive],patches=[patch]),tmp_path/'live-runs')
    assert path.read_bytes()==before


def test_old_bundle_unchanged(archive):
    bundle=build_bundle([archive]);assert bundle['schema']=='fork-investigation-bundle-v1'
    assert set(bundle['payload'])=={'runs','lenses','investigation'}
    validate_bundle(bundle)


def test_actual_tiny_cpu_patch_artifact_exports_against_synthetic_source(tmp_path,archive):
    """Real native random-model generation, with a synthetic scan fixture."""
    from types import MethodType
    from test_investigation import fixture
    from forking_paths.model import ForkingModel
    from fork_microscope.activation_patching import build_plan,run
    adapter,_,_,_=fixture()
    adapter.tokenizer.pad_token_id=0;adapter.gen_batch=1
    adapter.resample=MethodType(ForkingModel.resample,adapter)
    adapter._strip=MethodType(ForkingModel._strip,adapter)
    base=archive['records']['pass_1']['base']
    base['prompt_ids']=[1,2]
    base['base_text']=adapter.decode(base['gen_ids'])
    archive['base']['text']=base['base_text']
    archive['model']=dict(adapter.info)
    request=dict(source_run_id=archive['id'],source_pass_id='pass_1',
                 donor=dict(selection={'type':'original'},position=1),
                 recipient=dict(selection={'type':'original'},position=2),
                 layers=[0],samples=2,cont_max=2,temperature=1,seed=5,max_seconds=60,
                 selection_rationale='Tiny random-model integration test; not a research result.')
    plan=build_plan(adapter,archive,request);plan['id']='e'*32
    artifact=run(adapter,plan,lambda:None,lambda *args:None,lambda data:None)
    bundle=build_bundle([archive],patches=[artifact])
    imported=import_bundle(bundle,tmp_path/'live-runs')
    assert imported['patch_ids']==[artifact['id']]
    restored=json.loads((tmp_path/'investigations'/(artifact['id']+'.json')).read_text())
    assert restored['observations']==artifact['observations']
    assert restored['prefixes']==artifact['prefixes']


def test_patch_service_uses_job_lock_and_saves_cancelled_artifact(tmp_path,monkeypatch,patch):
    from fork_microscope import live_service
    from fork_microscope import activation_patching
    from types import SimpleNamespace
    monkeypatch.setattr(live_service,'RUNS',tmp_path/'live-runs')
    (tmp_path/'live-runs').mkdir()
    service=live_service.LiveService();service.model=SimpleNamespace(info=patch['model'])
    monkeypatch.setattr(service,'patch_plan',lambda request:copy.deepcopy(patch))
    def run(adapter,plan,check,progress,save):
        save(dict(plan,status='running',observations=[]))
        raise live_service.Cancelled()
    monkeypatch.setattr(activation_patching,'run',run)
    service.workflow_owner='another-workflow'
    with pytest.raises(ValueError,match='owns'):service.start('patch',{})
    service.workflow_owner=None
    job=service.start('patch',{})
    deadline=time.monotonic()+5
    while service.job['status']=='running' and time.monotonic()<deadline:time.sleep(.01)
    assert service.job['status']=='cancelled'
    assert service.investigation(job['job_id'])['status']=='cancelled'
