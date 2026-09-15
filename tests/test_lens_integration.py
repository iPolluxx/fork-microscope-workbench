# generated: Codex — no pretrained weights or fitted production lenses downloaded.
import copy
import hashlib
import json
from types import SimpleNamespace
import pytest
import torch
from jlens import JacobianLens
from fork_microscope import lens_integration as li
from test_investigation import fixture


def setup(tmp_path, matrices=None):
    adapter,base,result,_=fixture()
    width=adapter.model.config.hidden_size
    matrices=matrices or {0:torch.eye(width),1:torch.eye(width)}
    path=tmp_path/'test.pt'
    JacobianLens(matrices,n_prompts=10,d_model=width).save(str(path))
    meta=dict(model_id=adapter.info['model_id'],resolved_revision=adapter.info['resolved_revision'],
              lens_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),site=li.SITE,target_layer=1)
    (tmp_path/'test.pt.json').write_text(json.dumps(meta))
    request=dict(source_run_id=result['id'],source_pass_id='p',lens={'profile':'local','path':str(path)},
                 selection={'type':'original'},space='response',start=0,end=2,layers=[0,1],top_k=8)
    return adapter,base,result,request


def plan_run(adapter,result,request,get=lambda i:None):
    plan=li.build_plan(adapter,result,request,get)
    plan['id']='b'*32
    saves=[]
    out=li.run(adapter,plan,lambda:None,lambda *a:None,lambda x:saves.append(copy.deepcopy(x)))
    return plan,out,saves


def test_exact_tokens_identity_lens_baselines_and_no_mutation(tmp_path):
    a,b,r,q=setup(tmp_path)
    before_flags=[p.requires_grad for p in a.model.parameters()]
    before_mode=a.model.training;before_generation=a.model.generation_config.to_dict()
    a.tokenizer.add_bos_token=False
    seen=[]
    hook=a.model.register_forward_pre_hook(lambda m,args:seen.append(args[0].tolist()))
    plan,out,saves=plan_run(a,r,q)
    hook.remove()
    assert seen==[[[1,2,3,4,5]]]
    assert plan['arms'][0]['positions'][0]['absolute_position']==2
    assert len(out['cells'])==6 and out['status']=='complete'
    assert all(c['tokens']==c['logit_lens_tokens'] for c in out['cells'])
    assert out['parity'][0]['max_absolute_error']<1e-5
    assert saves[1]['status']=='running' and len(saves[1]['cells'])==3
    assert not a.tokenizer.add_bos_token and a.model.training==before_mode
    assert before_flags==[p.requires_grad for p in a.model.parameters()]
    assert a.model.generation_config.to_dict()==before_generation
    assert all(not layer._forward_hooks for layer in a.model.model.layers)
    # Earlier positions remain causal when including a later selected position.
    q['end']=0;_,short,_=plan_run(a,r,q)
    for c in short['cells']:
        longer=next(x for x in out['cells'] if x['layer']==c['layer'] and x['index']==0)
        assert [x['token_id'] for x in c['tokens']]==[x['token_id'] for x in longer['tokens']]
        torch.testing.assert_close(torch.tensor([x['score'] for x in c['tokens']]),torch.tensor([x['score'] for x in longer['tokens']]))


def test_non_symmetric_transport_matches_independent_calculation(tmp_path):
    matrix=torch.eye(16);matrix[0,1]=3;matrix[4,7]=-2
    a,b,r,q=setup(tmp_path,{0:matrix});q.update(layers=[0],start=1,end=1)
    _,out,_=plan_run(a,r,q)
    hidden=[]
    handle=a.model.model.layers[0].register_forward_hook(lambda m,args,output:hidden.append(output.detach()))
    with torch.no_grad():a.model(torch.tensor([[1,2,3,4]]))
    handle.remove()
    with torch.no_grad():
        transported=matrix@hidden[0][0,-1]
        expected=a.model.lm_head(a.model.model.norm(transported))
    values,ids=expected.topk(8)
    assert [x['token_id'] for x in out['cells'][0]['tokens']]==ids.tolist()
    torch.testing.assert_close(torch.tensor([x['score'] for x in out['cells'][0]['tokens']]),values)


def test_prompt_positions_saved_draw_and_edit_pair_selection(tmp_path):
    a,b,r,q=setup(tmp_path)
    q.update(space='prompt',start=0,end=1)
    plan,_,_=plan_run(a,r,q)
    assert plan['arms'][0]['input_ids']==[1,2]
    r['records']['p']['branches']=[dict(t=1,tok_id=9,draw_indices=[4],continuation_ids=[[10,11]])]
    q.update(space='response',end=3,selection={'type':'draw','checkpoint':1,'draw_index':4})
    p=li.build_plan(a,r,q,lambda i:None)
    assert p['arms'][0]['input_ids']==[1,2,3,9,10,11]
    edit={'request':{'kind':'edit','source_run_id':r['id'],'source_pass_id':'p'},'model':a.info,
          'source_ids_sha256':li.digest({'prompt_ids':b['prompt_ids'],'gen_ids':b['gen_ids']}),
          'arms':{'control':[3,4],'edit':[3,9]}}
    q.update(end=1,selection={'type':'edit_pair','investigation_id':'c'*32})
    p,out,_=plan_run(a,r,q,lambda i:edit)
    assert [x['input_ids'] for x in p['arms']]==[[1,2,3,4],[1,2,3,9]]
    assert {x['arm'] for x in out['cells']}=={'control','edit'}
    edit['source_ids_sha256']='wrong'
    with pytest.raises(ValueError,match='identity'):li.build_plan(a,r,q,lambda i:edit)


@pytest.mark.parametrize('change,match',[
    ({'end':100},'End'),({'layers':[0,0]},'unique'),({'layers':[2]},'Layer'),
    ({'top_k':0},'Top tokens'),({'space':'unknown'},'prompt'),
    ({'lens':{'profile':'qwen35_4b'}},'paired'),
])
def test_bad_requests_rejected_before_work(tmp_path,change,match):
    a,b,r,q=setup(tmp_path);q.update(change)
    with pytest.raises(ValueError,match=match):li.build_plan(a,r,q,lambda i:None)


def test_manifest_target_hash_width_and_nonfinite_validation(tmp_path):
    a,b,r,q=setup(tmp_path)
    m=tmp_path/'test.pt.json';meta=json.loads(m.read_text());meta['target_layer']=0;m.write_text(json.dumps(meta))
    with pytest.raises(ValueError,match='target_layer'):li.build_plan(a,r,q,lambda i:None)
    meta['target_layer']=1;meta['resolved_revision']='wrong';m.write_text(json.dumps(meta))
    with pytest.raises(ValueError,match='revision'):li.build_plan(a,r,q,lambda i:None)
    meta['resolved_revision']='pinned';meta['lens_sha256']='0'*64;m.write_text(json.dumps(meta))
    p=li.build_plan(a,r,q,lambda i:None);p['id']='b'*32
    with pytest.raises(ValueError,match='checksum'):li.run(a,p,lambda:None,lambda *a:None,lambda x:None)
    a,b,r,q=setup(tmp_path,{0:torch.eye(16)*float('nan')});q['layers']=[0]
    p=li.build_plan(a,r,q,lambda i:None);p['id']='b'*32
    with pytest.raises(ValueError,match='non-finite'):li.run(a,p,lambda:None,lambda *a:None,lambda x:None)
    a,b,r,q=setup(tmp_path,{0:torch.eye(3)});q['layers']=[0]
    p=li.build_plan(a,r,q,lambda i:None)
    with pytest.raises(ValueError,match='matrix'):li.load_checkpoint(p['lens'],16,[0],lambda:None,lambda *a:None)


def test_forward_failure_cleans_hooks_and_cancellation_saves_cells(tmp_path):
    a,b,r,q=setup(tmp_path);p=li.build_plan(a,r,q,lambda i:None);p['id']='b'*32
    def fail(*args,**kwargs):raise RuntimeError('injected')
    handle=a.model.model.layers[1].register_forward_hook(fail)
    with pytest.raises(RuntimeError,match='injected'):li.run(a,p,lambda:None,lambda *a:None,lambda x:None)
    assert not a.model.model.layers[0]._forward_hooks
    assert len(a.model.model.layers[1]._forward_hooks)==1
    handle.remove();saves=[]
    def check():
        if saves and saves[-1]['cells']:raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):
        li.run(a,p,check,lambda *a:None,lambda x:saves.append(copy.deepcopy(x)))
    assert len(saves[-1]['cells'])==3
    assert all(not layer._forward_hooks for layer in a.model.model.layers)


def test_final_parity_rejects_wrong_unembedding(tmp_path,monkeypatch):
    a,b,r,q=setup(tmp_path);p=li.build_plan(a,r,q,lambda i:None);p['id']='b'*32
    original=li.components
    def wrong(adapter):
        layers,width,unembed=original(adapter)
        return layers,width,lambda x:unembed(x)+1
    monkeypatch.setattr(li,'components',wrong)
    with pytest.raises(ValueError,match='does not match'):li.run(a,p,lambda:None,lambda *a:None,lambda x:None)


def test_service_job_guard_and_partial_status(tmp_path,monkeypatch):
    from fork_microscope import live_service
    a,b,r,q=setup(tmp_path)
    monkeypatch.setattr(live_service,'RUNS',tmp_path/'runs')
    s=live_service.LiveService(workspace_root=tmp_path/'workspace');s.model=a
    monkeypatch.setattr(s,'result',lambda *a,**kw:r)
    p=s.lens_plan(q);s.job={'id':'d'*32,'status':'running'}
    with pytest.raises(ValueError,match='current'):s.lens_plan(q)
    s._execute('lens',p)
    assert s.job['status']=='complete' and s.investigation('d'*32)['schema']=='fork-lens-v1'
    assert s.investigations()[0]['kind']=='lens'
    p['lens']['sha256']='0'*64;s.job={'id':'e'*32,'status':'running'}
    s._execute('lens',p)
    assert s.investigation('e'*32)['status']=='error'
    assert s.base is None


def test_muse_profile_requires_verified_revision():
    adapter=SimpleNamespace(info={'model_id':'meta-models/Muse-Glimmer-30B', 'resolved_revision':li.MUSE_REVISION})
    spec=li.lens_spec({'profile':'muse_glimmer'},adapter)
    assert spec['repo_id']=='eyes-ml/Muse-Glimmer-30B_jacobian-lens'
    assert spec['target_layer']==51 and spec['width']==6656
    adapter.info['resolved_revision']='another-revision'
    with pytest.raises(ValueError,match='exact verified model revision'):
        li.lens_spec({'profile':'muse_glimmer'},adapter)


def test_tiny_muse_native_logits_match_scaled_unembedding():
    from transformers import MuseGlimmerConfig, MuseGlimmerForConditionalGeneration
    config=MuseGlimmerConfig(
        text_config=dict(vocab_size=32,hidden_size=16,intermediate_size=32,num_hidden_layers=2,
                         num_attention_heads=2,num_key_value_heads=1,head_dim=8,
                         bos_token_id=1,eos_token_id=2,output_multiplier=0.19611613513818404),
        vision_config=dict(hidden_size=16,intermediate_size=32,num_hidden_layers=1,num_attention_heads=2),
        out_hidden_size=16,projector_hidden_size=16)
    config._attn_implementation='eager'
    model=MuseGlimmerForConditionalGeneration(config).eval()
    adapter=SimpleNamespace(model=model)
    layers,_,unembed=li.components(adapter)
    saved=[]
    hook=layers[-1].register_forward_hook(lambda m,args,out:saved.append(out[0] if isinstance(out,tuple) else out))
    try:
        with torch.no_grad():
            expected=model(input_ids=torch.tensor([[1,4,5]]),use_cache=False).logits
            actual=unembed(saved[0])
        torch.testing.assert_close(actual,expected,atol=1e-5,rtol=1e-5)
    finally:
        hook.remove()


def outcome_pair_fixture(tmp_path):
    a,b,r,q=setup(tmp_path)
    r['records']['p']['branches']=[
        dict(t=1,tok_id=9,draw_indices=[5,2],continuation_ids=[[10,11],[12,13]],
             observations=[dict(label='yes',stop_reason='eos'),dict(label='yes',stop_reason='eos')]),
        dict(t=1,tok_id=8,draw_indices=[3],continuation_ids=[[14,15,16]],
             observations=[dict(label='no',stop_reason='eos')]),
        dict(t=2,tok_id=7,draw_indices=[3],continuation_ids=[[17]],
             observations=[dict(label='yes',stop_reason='eos')]),
    ]
    q.update(start=0,end=3,selection=dict(type='draw_pair',checkpoint=1,draw_indices=[3,5]))
    return a,b,r,q


def test_outcome_pair_replays_exact_draws_and_preserves_provenance(tmp_path):
    a,b,r,q=outcome_pair_fixture(tmp_path);before=copy.deepcopy(r)
    seen=[]
    hook=a.model.register_forward_pre_hook(lambda m,args:seen.append(args[0].tolist()))
    try:plan,out,_=plan_run(a,r,q)
    finally:hook.remove()
    assert seen==[[[1,2,3,8,14,15]],[[1,2,3,9,10,11]]]
    assert r==before
    assert [arm['response_ids'] for arm in plan['arms']]==[[3,8,14,15,16],[3,9,10,11]]
    assert [arm['source_draw'] for arm in out['arms']]==[
        dict(checkpoint=1,draw_index=3,outcome='no',stop_reason='eos'),
        dict(checkpoint=1,draw_index=5,outcome='yes',stop_reason='eos')]
    assert len(out['cells'])==16
    assert {c['arm'] for c in out['cells']}=={'draw_a','draw_b'}
    # Their shared prefix has identical internal readouts; later inputs differ.
    for layer in q['layers']:
        cells=[c for c in out['cells'] if c['index']==0 and c['layer']==layer]
        assert cells[0]['tokens']==cells[1]['tokens']
    assert all(p['max_absolute_error']<1e-5 for p in out['parity'])
    assert any('semantic alignment' in w for w in out['warnings'])


@pytest.mark.parametrize('indices',[[3,3],[True,5],[3.0,5],[3],[],[3,99]])
def test_outcome_pair_rejects_invalid_or_missing_draw_ids(tmp_path,indices):
    a,b,r,q=outcome_pair_fixture(tmp_path);q['selection']['draw_indices']=indices
    with pytest.raises(ValueError):li.build_plan(a,r,q,lambda i:None)


@pytest.mark.parametrize('fault',['same_label','capped','unknown_completion','other','missing_ids','duplicate_draw','shorter_arm'])
def test_outcome_pair_requires_finished_distinct_exact_trajectories(tmp_path,fault):
    a,b,r,q=outcome_pair_fixture(tmp_path);branch=r['records']['p']['branches'][1]
    if fault=='same_label':branch['observations'][0]['label']='yes'
    elif fault=='capped':branch['observations'][0]['stop_reason']='length'
    elif fault=='unknown_completion':branch['observations'][0].pop('stop_reason')
    elif fault=='other':branch['observations'][0]['label']='Other'
    elif fault=='missing_ids':branch.pop('continuation_ids')
    elif fault=='duplicate_draw':r['records']['p']['branches'].append(copy.deepcopy(branch))
    elif fault=='shorter_arm':q['end']=4
    with pytest.raises(ValueError):li.build_plan(a,r,q,lambda i:None)


def test_cache_reuses_narrower_inspection_without_forward_and_matches_fresh(tmp_path):
    a,b,r,q=setup(tmp_path)
    _,first,_=plan_run(a,r,q)
    assert first['execution']['forward_passes']==1
    q.update(start=1,end=1,layers=[0],top_k=4)
    planned=li.build_plan(a,r,q,lambda i:None)
    assert planned['work']['forward_passes']==0
    calls=[]
    handle=a.model.register_forward_pre_hook(lambda *args:calls.append(True))
    _,cached,_=plan_run(a,r,q)
    handle.remove()
    assert not calls and cached['execution']['forward_passes']==0
    assert cached['execution']['reused_activation_rows']==2
    assert cached['parity'][0]['max_absolute_error'] is None
    a._lens_activation_cache=li.ActivationCache()
    _,fresh,_=plan_run(a,r,q)
    assert fresh['execution']['forward_passes']==1
    for field in ('tokens','logit_lens_tokens','model_tokens'):
        x,y=cached['cells'][0][field],fresh['cells'][0][field]
        assert [v['token_id'] for v in x]==[v['token_id'] for v in y]
        torch.testing.assert_close(torch.tensor([v['score'] for v in x]),torch.tensor([v['score'] for v in y]),atol=1e-6,rtol=1e-5)


def test_cache_expansion_prefix_identity_and_parameter_change(tmp_path):
    a,b,r,q=setup(tmp_path)
    q.update(start=0,end=0,layers=[0])
    plan_run(a,r,q)
    q.update(end=2)
    _,expanded,_=plan_run(a,r,q)
    assert expanded['execution']==dict(forward_passes=1,replay_tokens=5,reused_activation_rows=2,captured_activation_rows=4)
    # Same position and token, but a different preceding prompt, cannot reuse.
    b['prompt_ids'][0]=7
    _,changed,_=plan_run(a,r,q)
    assert changed['execution']['reused_activation_rows']==0
    with torch.no_grad():next(a.model.parameters()).add_(.001)
    assert li.build_plan(a,r,q,lambda i:None)['work']['forward_passes']==1
    _,updated,_=plan_run(a,r,q)
    assert updated['execution']['reused_activation_rows']==0


def test_shared_pair_prefix_is_reused_and_divergent_states_are_separate(tmp_path):
    a,b,r,q=outcome_pair_fixture(tmp_path)
    p,out,_=plan_run(a,r,q)
    assert p['comparison']['first_different_token']==1
    assert p['comparison']['baseline_index']==0
    assert out['execution']['forward_passes']==2
    assert out['execution']['reused_activation_rows']==2  # two layers at shared response position 0
    q.update(space='prompt',start=1,end=1)
    # Prompt position was not captured in the previous readout.
    p,out,_=plan_run(a,r,q)
    assert p['work']['forward_passes']==1
    assert out['execution']['forward_passes']==1
    assert out['cells'][0]['tokens']==out['cells'][2]['tokens']


def test_cache_eviction_is_bounded_and_dynamic_rope_disables_reuse(tmp_path):
    cache=li.ActivationCache(limit=128)
    for i in range(10):cache.put(i,torch.ones(16))
    assert cache.bytes==128 and len(cache.rows)==2 and cache.get(0) is None
    assert cache.get(8) is not None
    cache.put(10,torch.ones(16))
    assert cache.get(9) is None and cache.get(8) is not None
    a,b,r,q=setup(tmp_path)
    a.model.config.rope_scaling={'rope_type':'dynamic','factor':2}
    plan_run(a,r,q)
    assert li.activation_cache(a).limit==0
    assert li.build_plan(a,r,q,lambda i:None)['work']['forward_passes']==1
    a.model.train()
    with pytest.raises(ValueError,match='evaluation'):li.build_plan(a,r,q,lambda i:None)


def test_comparison_handles_first_token_and_unequal_length_prefixes():
    arm=lambda ids:dict(prompt_ids=[1,2],response_ids=ids)
    region=li.comparison_region([arm([7,8]),arm([9,8])])
    assert region['first_different_token']==0 and region['baseline_space']=='prompt' and region['baseline_index']==1
    region=li.comparison_region([arm([7]),arm([7,8])])
    assert region['first_different_token'] is None and region['prefix_only']
