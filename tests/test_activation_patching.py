"""CPU random-model tests: no pretrained weights or paid model execution."""
import copy
from types import MethodType
import pytest
import torch

from fork_microscope.activation_patching import ARMS, build_plan, options, run, _capture, _patch
from test_investigation import fixture
from forking_paths.model import ForkingModel


def setup():
    a, base, result, common = fixture()
    a.tokenizer.pad_token_id = 0
    a.gen_batch = 1
    a.resample = MethodType(ForkingModel.resample, a)
    a._strip = MethodType(ForkingModel._strip, a)
    result['records']['p']['branches'] = [dict(t=1, tok_id=9, draw_indices=[0], continuation_ids=[[10,11,12]],
        observations=[dict(label='8', stop_reason='eos')])]
    q = dict(common, donor={'selection':{'type':'draw','checkpoint':1,'draw_index':0},'position':2},
        recipient={'selection':{'type':'original'},'position':2}, layers=[0], samples=2,
        cont_max=3, temperature=1., seed=7, max_seconds=120,
        selection_rationale='Compare the distinct saved prefixes after their first unequal token.')
    return a, result, q


def test_plan_resolves_exact_ids_and_bounds():
    a, r, q = setup(); before = copy.deepcopy(r)
    p = build_plan(a,r,q)
    assert p['prefixes']['donor']['input_ids'] == [1,2,3,9,10]
    assert p['prefixes']['recipient']['input_ids'] == [1,2,3,4,5]
    assert p['prefixes']['recipient']['position'] == 2
    assert p['prefixes']['recipient']['absolute_position'] == 4
    assert p['max_new_tokens'] == 18 and p['continuations'] == 6 and r == before
    for key,value in [('samples',33),('cont_max',513),('max_seconds',1801),('layers',[0,0]),('selection_rationale',''),('temperature',float('nan'))]:
        with pytest.raises(ValueError):build_plan(a,r,dict(q,**{key:value}))
    with pytest.raises(ValueError):build_plan(a,r,dict(q,module_path='model.danger'))
    q['donor']['position']=0; q['recipient']['position']=0
    with pytest.raises(ValueError,match='identical'):build_plan(a,r,q)


def test_availability_and_source_identity_checks():
    assert not options(None)['available']
    a,r,q = setup()
    a.model.train()
    assert not options(a)['available']
    a.model.eval(); a.model.generation_config.use_cache=False
    with pytest.raises(ValueError,match='cache'):build_plan(a,r,q)
    a.model.generation_config.use_cache=True
    a.info['resolved_revision']='different'
    with pytest.raises(ValueError,match='exact'):build_plan(a,r,q)
    a.info['resolved_revision']='pinned'; q['recipient']['position']=100
    with pytest.raises(ValueError):build_plan(a,r,q)


def test_patch_touches_only_final_prefill_row_then_leaves_decode_alone():
    a,r,q = setup(); p=build_plan(a,r,q)
    donor = _capture(a,p['prefixes']['donor']['input_ids'],[0],lambda:None)
    prefix=p['prefixes']['recipient']['input_ids']
    model=a.model; ids=torch.tensor([prefix])
    with torch.no_grad():baseline=model(ids,use_cache=True).logits.clone()
    rows=[]
    # Register observation after patch so it sees the substituted output.
    with _patch(a,prefix,donor,[0],lambda:None) as calls:
        h=model.model.layers[0].register_forward_hook(lambda m,args,out: rows.append(out.detach().clone() if isinstance(out,torch.Tensor) else out[0].detach().clone()))
        try:
            with torch.no_grad():
                output=model(ids,use_cache=True)
                model(torch.tensor([[6]]),past_key_values=output.past_key_values,use_cache=True)
        finally:h.remove()
    assert calls=={0:2}
    torch.testing.assert_close(rows[0][0,-1],donor[0])
    clean=[]
    h=model.model.layers[0].register_forward_hook(lambda m,args,out:clean.append(out.detach().clone() if isinstance(out,torch.Tensor) else out[0].detach().clone()))
    with torch.no_grad():after=model(ids,use_cache=True).logits.clone()
    h.remove()
    torch.testing.assert_close(rows[0][0,:-1],clean[0][0,:-1],rtol=0,atol=0)
    assert not torch.equal(baseline,output.logits)
    torch.testing.assert_close(baseline,after,rtol=0,atol=0)
    assert all(not layer._forward_hooks for layer in model.model.layers)


def test_complete_controls_classification_provenance_and_no_persistent_changes():
    a,r,q=setup();q['layers']=[0,1];p=build_plan(a,r,q)
    config=copy.deepcopy(a.model.generation_config.to_dict())
    weights={k:v.clone() for k,v in a.model.state_dict().items()}
    saves=[]
    out=run(a,p,lambda:None,lambda *a:None,lambda x:saves.append(copy.deepcopy(x)))
    assert out['status']=='complete'
    assert [o['arm'] for o in out['observations']]==list(ARMS)*2
    assert [o['seed'] for o in out['observations']]==[7,7,7,8,8,8]
    assert out['summary']['identity_control']==dict(draws_compared=2,exact_matches=2,passed=True)
    assert all(sum(out['summary']['counts'][arm].values())==2 for arm in ARMS)
    assert len(out['activation_summary'])==4
    assert saves[1]['status']=='running' and len(saves[1]['observations'])==1
    assert all('vector' not in row for row in out['activation_summary'])
    assert all(not layer._forward_hooks for layer in a.model.model.layers)
    assert config==a.model.generation_config.to_dict() and not a.model.training
    for key,value in a.model.state_dict().items():torch.testing.assert_close(value,weights[key],rtol=0,atol=0)


def test_failure_and_mid_generation_cancellation_remove_hooks_and_save_partial():
    a,r,q=setup();p=build_plan(a,r,q)
    original=a.resample
    def fail(*args,**kw):raise RuntimeError('injected generation failure')
    a.resample=fail
    with pytest.raises(RuntimeError,match='injected'):run(a,p,lambda:None,lambda *a:None,lambda x:None)
    assert all(not layer._forward_hooks for layer in a.model.model.layers)
    a.resample=original;saves=[]
    def check():
        if saves and saves[-1]['observations']:raise RuntimeError('cancel requested')
    with pytest.raises(RuntimeError,match='cancel'):
        run(a,p,check,lambda *a:None,lambda x:saves.append(copy.deepcopy(x)))
    assert len(saves[-1]['observations'])==1
    assert all(not layer._forward_hooks for layer in a.model.model.layers)
    ticks=[0]
    def mid_check():
        ticks[0]+=1
        if ticks[0]==2:raise RuntimeError('cancel mid-generation')
    with pytest.raises(RuntimeError,match='mid-generation'):
        with _patch(a,p['prefixes']['recipient']['input_ids'],None,[0],mid_check):
            original([p['prefixes']['recipient']['input_ids']],n=1,max_tokens=4,temperature=1,seed=2)
    assert all(not layer._forward_hooks for layer in a.model.model.layers)


def test_uncached_generator_rejected_and_time_limit_cooperative(monkeypatch):
    a,r,q=setup();p=build_plan(a,r,q);prefix=p['prefixes']['recipient']['input_ids']
    with pytest.raises(ValueError,match='KV cache'):
        with _patch(a,prefix,None,[0],lambda:None):
            with torch.no_grad():
                a.model(torch.tensor([prefix]),use_cache=False)
                a.model(torch.tensor([prefix+[3]]),use_cache=False)
    assert all(not layer._forward_hooks for layer in a.model.model.layers)
    clock=iter([0,121])
    monkeypatch.setattr('fork_microscope.activation_patching.time.monotonic',lambda:next(clock))
    with pytest.raises(TimeoutError,match='time limit'):run(a,p,lambda:None,lambda *a:None,lambda x:None)
