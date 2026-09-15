import json
from types import SimpleNamespace
import numpy as np
import pytest
from fork_microscope.sampling import pass_plan, allocation, position_draws
from fork_microscope.live_service import reconstruct, LiveService
from fork_microscope.outcome_readout import inspect_continuation, muse_answer

CONFIG=dict(passes=[dict(id='pass_1',label='Pass 1',start=0,end=15,stride=4,offset=0,samples=10,seed=0)],cont_max=8,temperature=1,top_k=2,threshold=.05,dense=False,reference_samples=10,tuning='cv')

def test_one_pass_and_independent_offsets():
    assert pass_plan(CONFIG,20)[0]['positions']==[0,4,8,12]
    second=CONFIG['passes'][0]|dict(id='pass_2',offset=1,stride=3,end=17)
    plan=pass_plan(CONFIG|dict(passes=CONFIG['passes']+[second]),20)
    assert plan[1]['positions']==[1,4,7,10,13,16]
    for key,value in [('id','../x'),('offset',4),('samples',True),('end',100)]:
        with pytest.raises(ValueError):pass_plan(CONFIG|dict(passes=[CONFIG['passes'][0]|{key:value}]),20)
    with pytest.raises(ValueError):pass_plan(CONFIG|dict(passes=CONFIG['passes']*2),20)
    with pytest.raises(ValueError):pass_plan(CONFIG|dict(passes=[]),20)

def test_branch_allocation_is_weighted_and_repeatable():
    branches=[SimpleNamespace(tok_p=.6),SimpleNamespace(tok_p=.4)]
    a=allocation(branches,10000,[1,2,3])
    assert a==allocation(branches,10000,[1,2,3])
    assert abs(a.count(0)/len(a)-.6)<.02

def mixture_record(cap_hits=0):
    r=dict(sampling_design='position_mixture_v1',categories=['A','B','C','D','Other'],
        base={'finish_reason':'stop'},config={'cont_temperature':1},positions=[],branches=[])
    for t in [0,4,8,12]:
        r['positions'].append(dict(t=t,samples=10,retained_mass=1))
        for tok,indices,label in [(1,[0,2,4,6,8,9],'A'),(2,[1,3,5,7],'B')]:
            r['branches'].append(dict(t=t,tok_id=tok,answers=[label]*len(indices),draw_indices=indices,
                observations=[dict(stop_reason='length' if i<cap_hits else 'eos') for i in indices]))
    return r

def test_every_collected_outcome_is_used_once_in_fit():
    r=mixture_record();pos,draws=position_draws(r)
    assert draws.shape==(4,10)
    f=reconstruct(r,10,'cv',123)
    np.testing.assert_allclose(f['raw'],[[.6,.4,0,0,0]]*4)
    assert f['raw']==f['weighted']
    assert f['mixture_diagnostics']['all_collected_draws_used']
    assert f['cv_candidates']>0
    assert f['segmentation_enabled'] is True
    assert f['bands_kind']=='model_based_dirichlet_marginal'
    r['branches'][0]['draw_indices'][0]=2
    with pytest.raises(ValueError,match='Duplicate'):position_draws(r)

def test_completion_gate_and_short_grid_not_misleading():
    r=mixture_record(cap_hits=2)
    f=reconstruct(r,10,'cv',123)
    assert f['fit_status']=='withheld' and f['support']==[] and f['parameters'] is None
    assert f['segmentation_enabled'] is False and f['bands_kind'] is None
    r=mixture_record();r['positions']=r['positions'][:2];r['branches']=r['branches'][:4]
    f=reconstruct(r,10,'cv',123)
    assert f['tuning']=='fixed' and f['cv_candidates']==0 and not f['boundaries']
    assert f['segmentation_enabled'] is False

def test_generic_cap_cannot_become_a_confident_answer():
    tok=SimpleNamespace(decode=lambda *a,**k:'The answer is (A)')
    model=SimpleNamespace(tokenizer=tok,eos_ids=[99],is_muse=False)
    base=SimpleNamespace(gen_ids=[1]);branch=SimpleNamespace(idx=0,tok_id=2)
    capped=inspect_continuation(model,base,branch,[1,2],2)
    assert capped['label']=='Other' and capped['label_source']=='incomplete'
    completed=inspect_continuation(model,base,branch,[1],2)
    assert completed['label']=='A' and completed['label_source']=='completed_regex'

def test_muse_alternate_eos_and_incomplete_reply():
    assert muse_answer('to=user<|message|>The answer is (B)<|end_of_text|>')=='B'
    model=SimpleNamespace(tokenizer=SimpleNamespace(decode=lambda *a,**k:'to=user<|message|>The answer is (B)'),eos_ids=[99],is_muse=True)
    base=SimpleNamespace(gen_ids=[1]);branch=SimpleNamespace(idx=0,tok_id=2)
    assert inspect_continuation(model,base,branch,[1,2],2)['label']=='Other'
    assert inspect_continuation(model,base,branch,[1],2)['label']=='B'

@pytest.mark.parametrize('custom_answers', [None, ['answer is (A)', 'answer is (B)'], ['answer is (A)', 'answer is (B)', 'x', 'y', 'z', 'w']])
def test_multibranch_collection_roundtrip(tmp_path,monkeypatch,custom_answers):
    from fork_microscope import live_service as module
    from forking_paths.model import BasePath
    monkeypatch.setattr(module,'RUNS',tmp_path)
    class Tokenizer:
        def decode(self,ids,**kwargs):return 'The answer is (A)' if ids[-1]==7 else 'The answer is (B)'
    calls=[]
    class Model:
        eos_ids=[99];is_muse=False;tokenizer=Tokenizer()
        info={'context_limit':1000,'model_id':'fixture','vocab_size':20}
        model=SimpleNamespace(generation_config=SimpleNamespace(to_dict=lambda:{}))
        def decode(self,ids):return self.tokenizer.decode(ids)
        def draw_branch(self,branch,count,cap,temp,seed,check):
            calls.append(count);return [[7 if branch.tok_id==10 else 8]]*count
    s=LiveService();s.model=Model();s.base=BasePath([1],[10]*16,[[10,11]]*16,[[np.log(.6),np.log(.4)]]*16,'stop')
    s.question={'question':'fixture','choices':['a','b','c','d']};s.base_config={'max_tokens':16};s.job={'id':'a'*32}
    if custom_answers is not None: s.question={'question':'fixture','answers':custom_answers,'matching':'answer_text_anywhere_v1'}
    s.collect(CONFIG|{'dense':True})
    r=s.result('a'*32,raw=True)
    assert r['categories']==(custom_answers or ['A','B','C','D'])+['Other']
    assert len(r['passes'][0]['curve']['raw'][0])==len(r['categories'])
    assert len(r['reference']['values'][0])==len(r['categories'])
    assert sum(calls)==170 # 4*10 + 13*10 reference
    assert len(r['passes'])==1 and r['schema_version']==2
    assert r['measured']['pass_1']['continuations']==40
    assert r['passes'][0]['curve']['comparison']['compared_positions']==13
    for key in ['pass_1','dense']:
        rec=r['records'][key];_,draws=position_draws(rec)
        assert draws.size==sum(len(b['answers']) for b in rec['branches'])
        assert all(b['observations'][0]['full_response_text'] for b in rec['branches'])


def test_dashboard_module_is_served_and_root_opens_prompt_workspace():
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from fork_microscope.microscope_server import Handler
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        url=f'http://127.0.0.1:{server.server_port}'
        with urllib.request.urlopen(url+'/passes.mjs') as response:
            assert response.status==200 and b'export function newPass' in response.read()
        with urllib.request.urlopen(url+'/') as response:
            html=response.read()
            assert b'workspace.mjs' in html and b'worker-connection.js' in html
        with urllib.request.urlopen(url+'/live.html') as response:
            html=response.read()
            assert b'id="add-pass"' in html and b'id="continuations"' in html
        request=urllib.request.Request(url+'/api/live/run',data=b'{}',headers={'Content-Type':'application/json','Origin':url})
        with pytest.raises(urllib.error.HTTPError) as exc: urllib.request.urlopen(request)
        assert exc.value.code==400
    finally:
        server.shutdown();server.server_close();thread.join()


@pytest.mark.parametrize('reply,expected', [
    ('The answer is 56.', ['56']),
    ('156 and 560', []),
    ('I reject 54 and choose 56.', ['54', '56']),
    ('I reject 54.', ['54']),  # mention matching intentionally does not judge truth
    ('New   York is my answer.', ['New York']),
    ('new yorkshire', []),
    ('56 then 56', ['56']),
    ('The answer is fifty-six.', []),
])
def test_literal_answer_matching(reply, expected):
    from fork_microscope.outcome_readout import match_answer_text
    assert match_answer_text(reply, ['54','56','New York'])==expected


@pytest.mark.parametrize('answers', [[], ['a','A'], ['Other'], ['  '], ['a\nb'], [7], ['x']*33])
def test_invalid_custom_answers(answers):
    from fork_microscope.outcome_readout import validate_answers
    with pytest.raises(ValueError): validate_answers(answers)


def test_custom_matching_completion_and_channel_scope():
    texts=['<think>54</think>56','<think>56',
           'analysis 54 to=user<|message|>56','analysis 56', '54 or 56']
    base=SimpleNamespace(gen_ids=[1]);branch=SimpleNamespace(idx=0,tok_id=2)
    for raw,muse,label,source in [(texts[0],False,'56','answer_text'),
                                  (texts[1],False,'Other','unparsed'),
                                  (texts[2],True,'56','answer_text'),
                                  (texts[3],True,'Other','unparsed'),
                                  (texts[4],False,'Other','ambiguous')]:
        model=SimpleNamespace(tokenizer=SimpleNamespace(decode=lambda *a,**k:raw),eos_ids=[99],is_muse=muse)
        o=inspect_continuation(model,base,branch,[1],2,['54','56'])
        assert (o['label'],o['label_source'])==(label,source)
        if source=='ambiguous': assert o['matched_answers']==['54','56']
        capped=inspect_continuation(model,base,branch,[1,2],2,['54','56'])
        assert capped['label']=='Other' and capped['reply_text'] is None


def test_custom_prompt_is_not_modified_by_tracking_answers():
    from fork_microscope.live_model import AttachedModel
    messages=[]
    adapter=AttachedModel.__new__(AttachedModel)
    def template(value, **kwargs):
        messages.extend(value)
        assert kwargs['return_dict'] is False
        return [1,2,3]
    adapter.tokenizer=SimpleNamespace(chat_template='native',apply_chat_template=template)
    assert adapter.prompt_text('My exact prompt.', 'chat')==[1,2,3]
    assert messages==[{'role':'user','content':'My exact prompt.'}]


@pytest.mark.parametrize('raw,muse,finish,status,label',[
    ('FINAL DECISION=DEFER_REPAIR',False,'stop','matched','DEFER_REPAIR'),
    ('APPROVE_REPAIR or DEFER_REPAIR',False,'stop','ambiguous','Other'),
    ('I prefer waiting.',False,'stop','unmatched','Other'),
    ('DEFER_REPAIR',False,'length','incomplete','Other'),
    ('<think>APPROVE_REPAIR</think>DEFER_REPAIR',False,'stop','matched','DEFER_REPAIR'),
    ('analysis APPROVE_REPAIR to=user<|message|>DEFER_REPAIR',True,'stop','matched','DEFER_REPAIR'),
    ('analysis APPROVE_REPAIR',True,'stop','unmatched','Other'),
])
def test_base_preview_uses_completed_reply_matching(raw,muse,finish,status,label):
    from fork_microscope.outcome_readout import inspect_base
    model=SimpleNamespace(is_muse=muse,tokenizer=SimpleNamespace(decode=lambda *a,**k:raw))
    result=inspect_base(model,SimpleNamespace(finish_reason=finish,gen_ids=[1,2]),['APPROVE_REPAIR','DEFER_REPAIR'])
    assert (result['status'],result['label'])==(status,label)
