# generated: Codex — CPU-only investigation metadata, search, persistence and portability tests.
import copy
import json
import threading
import time
from types import SimpleNamespace
import pytest
from test_evidence_io import archive
from test_workflow import FakeService, finished
from fork_microscope.investigation_workflow import WorkflowManager
from fork_microscope.investigation_records import classify, make_response, validate_context
from fork_microscope.investigation_bundle import build_bundle, validate_bundle, import_bundle, digest

RULE = {'schema':'fork-outcome-rule-v1','method':'final_marker','answers':['A','B'],'marker':'Final answer:'}
CONTEXT = {'name':'Choice','question':'Which outcome?', 'input':{'schema':'fork-input-v1','prompt':'Choose','mode':'chat'},'outcome_rule':RULE}

class SearchService(FakeService):
    def __init__(self, archive, replies, delay=0):
        super().__init__(archive, delay)
        self.replies = iter(replies); self.responses_saved = {}; self.selected = None
        self.model = SimpleNamespace(info={'model_id':'test/model','resolved_revision':'test-revision','vocab_size':100}, is_muse=False)
    def start(self, action, payload, owner=None, job_id=None):
        if action not in ('base','select_response'): return super().start(action,payload,owner,job_id)
        self.calls.append(action);self.job={'id':job_id,'status':'running'}
        def finish():
            time.sleep(self.delay)
            if self.job['status'] != 'running': return
            if action == 'base':
                text, finish_reason = next(self.replies)
                self.model.decode = lambda ids:text
                self.model.tokenizer=SimpleNamespace(decode=lambda ids,**kw:text)
                base=SimpleNamespace(prompt_ids=[42],gen_ids=[1,2,3],finish_reason=finish_reason)
                self.base=base
                response=make_response(self.model,base,payload,job_id,owner)
                self.responses_saved[job_id]=response;self.job['response_id']=job_id
            else:self.selected=self.responses_saved[payload['response_id']]['base']
            self.job['status']='complete'
        threading.Thread(target=finish,daemon=True).start()
    def response(self, id): return copy.deepcopy(self.responses_saved[id])


def setup(tmp_path,archive,replies,limits=None,delay=0):
    service=SearchService(archive,replies,delay);manager=WorkflowManager(service,tmp_path/'workflow-jobs')
    d=manager.create({'request_id':'draft','context':copy.deepcopy(CONTEXT),'limits':limits or dict(max_seconds=60,max_samples=100,max_generated_tokens=1000,max_attempts=10)})
    return service,manager,d


def search(manager,id,**overrides):
    request=dict(id=id,request_id='search',target='B',max_attempts=3,max_tokens=16,seed=1,temperature=.7)
    request.update(overrides)
    manager.search(request)
    return finished(manager,id),request

@pytest.mark.parametrize('text,complete,status,label',[
    ('We mention A.\nFinal answer: B',True,'matched','B'),
    ('Final answer: A\nFinal answer: B',True,'ambiguous','Other'),
    ('I would mention Final answer: B here.',True,'unmatched','Other'),
    ('Final answer: B',False,'incomplete','Other'),
    ('final ANSWER:  b ',True,'matched','B'),
])
def test_preview_exact_final_marker_semantics(text,complete,status,label):
    result=classify(RULE,text,complete)
    assert (result['status'],result['label'])==(status,label)

@pytest.mark.parametrize('replies,reason,n',[
    ([('Final answer: A','stop'),('Final answer: B','stop')],'target_found',2),
    ([('Final answer: A','stop')]*3,'target_not_found',3),
    ([('Final answer: B','length')]*3,'target_not_found',3),
    ([('Final answer: A\nFinal answer: B','stop')]*3,'target_not_found',3),
])
def test_search_stops_saves_every_attempt_and_is_idempotent(tmp_path,archive,replies,reason,n):
    service,manager,d=setup(tmp_path,archive,replies)
    result,request=search(manager,d['id'])
    assert result['status']=='idle',result.get('message')
    assert result['searches'][0]['completion_reason']==reason
    assert len(result['responses'])==n and len(service.responses_saved)==n
    assert result['reservations']==dict(samples=0,generated_tokens=n*16,attempts=n)
    assert all(service.response(i)['classification']==classify(RULE,service.response(i)['raw_text'],service.response(i)['base']['finish_reason']=='stop') for i in result['responses'])
    manager.search(request);assert len(service.calls)==n
    request['seed']=2
    with pytest.raises(ValueError,match='Request ID'):manager.search(request)
    bundle=manager.export(d['id']);assert bundle['schema']=='fork-investigation-bundle-v3'
    assert bundle['manifest']['entry_run_id'] is None
    import_bundle(bundle,tmp_path/'copy'/'live-runs')
    assert import_bundle(bundle,tmp_path/'copy'/'live-runs')['already_present']


def test_generation_scan_share_reservation_and_exact_selection(tmp_path,archive):
    service,manager,d=setup(tmp_path,archive,[('Final answer: B','stop')])
    result,_=search(manager,d['id'],target=None,max_attempts=1,temperature=0)
    rid=result['responses'][0]
    request=dict(id=d['id'],request_id='scan',action='scan',payload={'response_id':rid,'settings':{}})
    manager.operation(request);result=finished(manager,d['id'])
    assert result['status']=='idle',result.get('message')
    assert service.selected==service.responses_saved[rid]['base']
    assert result['reservations']==dict(samples=20,generated_tokens=216,attempts=1)
    assert len(result['runs'])==1
    manager.operation(request);assert service.calls==['base','select_response','run']


def test_budget_exhaustion_and_cancel(tmp_path,archive):
    service,manager,d=setup(tmp_path,archive,[('Final answer: A','stop')],dict(max_seconds=60,max_samples=10,max_generated_tokens=16,max_attempts=10))
    result,_=search(manager,d['id'])
    assert result['status']=='budget_exhausted' and service.calls==['base']
    # Reservation for second generation was rejected before execution.
    assert result['reservations']['generated_tokens']==16
    service,manager,d=setup(tmp_path/'cancel',archive,[('Final answer: A','stop')],delay=.5)
    manager.search(dict(id=d['id'],request_id='cancel',target='B',max_attempts=3,max_tokens=16,seed=0,temperature=.7))
    manager.cancel(d['id']);result=finished(manager,d['id'])
    assert result['status']=='cancelled' and service.workflow_owner is None


def test_optimistic_metadata_and_structured_history(tmp_path,archive):
    _,manager,d=setup(tmp_path,archive,[])
    context=copy.deepcopy(CONTEXT);context['input']['messages']=[{'role':'system','content':'Be brief'},{'role':'user','content':'Earlier question'},{'role':'assistant','content':'Earlier answer'}]
    updated=manager.update({'id':d['id'],'record_revision':0,'context':context,'conclusion':'No claim yet'})
    assert updated['record_revision']==1 and updated['context']['input']['messages']==context['input']['messages']
    with pytest.raises(ValueError,match='changed'):manager.update({'id':d['id'],'record_revision':0,'conclusion':'overwrite'})
    context['input']['messages'][0]['role']='tool'
    with pytest.raises(ValueError,match='role'):validate_context(context)


def test_restart_marks_search_interrupted_never_regenerates(tmp_path,archive):
    service,manager,d=setup(tmp_path,archive,[])
    d.update(status='running');d['operations']=[{'status':'running'}];d['searches']=[{'status':'running','response_ids':[]}]
    d['reservations']['generated_tokens']=16;manager.save(d)
    restored=WorkflowManager(service,manager.root)
    result=restored.read(d['id'])
    assert result['status']=='interrupted' and result['searches'][0]['completion_reason']=='interrupted'
    assert result['reservations']['generated_tokens']==16 and not service.calls
    with pytest.raises(ValueError,match='cannot be retried'):restored.resume(d['id'])


def test_v3_rejects_fabricated_response_before_writes(tmp_path,archive):
    service,manager,d=setup(tmp_path,archive,[('Final answer: B','stop')]);search(manager,d['id'],max_attempts=1)
    bundle=manager.export(d['id']);bundle['payload']['responses'][0]['base']['gen_ids'][0]=99
    bundle['sha256']=digest(bundle['payload'])
    with pytest.raises(ValueError,match='checksum'):import_bundle(bundle,tmp_path/'copy'/'live-runs')
    assert not (tmp_path/'copy').exists()


def test_v1_metadata_upgrade_keeps_backup_and_evidence(tmp_path,archive):
    from test_workflow import config
    service=SearchService(archive,[]);manager=WorkflowManager(service,tmp_path)
    legacy=dict(id='d'*32,schema='fork-workflow-v1',status='complete',config=None,runs=[archive['id']],lenses=[],created=1,steps=[],reservations={},elapsed_seconds=1)
    manager.save(legacy);before=(tmp_path/(legacy['id']+'.json')).read_bytes()
    upgraded=manager.update({'id':legacy['id'],'conclusion':'Recorded conclusion'})
    assert upgraded['schema']=='fork-workflow-v2' and upgraded['runs']==legacy['runs']
    assert (tmp_path/(legacy['id']+'.v1-backup')).read_bytes()==before


def test_continuation_edit_and_capture_use_selected_exact_ids():
    from test_investigation import fixture
    from fork_microscope.investigation import build_plan, source
    a, base, run, common=fixture()
    run['records']['p']['branches']=[dict(t=2,tok_id=9,continuation_ids=[[10,11]],draw_indices=[7])]
    selector=dict(schema='fork-trajectory-v1',type='draw',checkpoint=2,draw_index=7)
    edit=dict(common,kind='edit',source_selection=selector,start=2,end=4,replacement='12',samples=2,cont_max=3,temperature=1,seed=0)
    plan=build_plan(a,run,edit)
    assert plan['source_base']['gen_ids']==[3,4,9,10,11]
    assert plan['arms']=={'control':[3,4,9,10],'edit':[3,4,12]}
    capture=build_plan(a,run,dict(common,kind='activation',source_selection=selector,positions=[3],layers=[0]))
    assert capture['source_base']['gen_ids']==[3,4,9,10,11]
    edit['source_selection']=dict(selector,draw_index=8)
    with pytest.raises(ValueError,match='missing'):build_plan(a,run,edit)


def basic_artifacts(archive):
    from fork_microscope.investigation_records import exact_hash
    base=archive['records']['pass_1']['base'];common=dict(source_run_id=archive['id'],source_pass_id='pass_1')
    provenance=dict(schema='fork-investigation-v1',status='complete',model=archive['model'],source_base=base,
                    source_ids_sha256=exact_hash({k:base[k] for k in ('prompt_ids','gen_ids')}))
    prefix=base['prompt_ids']+base['gen_ids'][:2]
    capture=dict(provenance,id='c'*32,request=dict(common,kind='activation',positions=[2],layers=[0]),
        captures=[dict(checkpoint=2,layer=0,prefix_ids=prefix,prefix_sha256=exact_hash(prefix),absolute_position=len(prefix)-1,vector=[.5,.2],norm=(.29)**.5)])
    edit=dict(provenance,id='e'*32,request=dict(common,kind='edit',start=1,end=2,replacement='x',samples=2,cont_max=3,temperature=1,seed=0),
        replacement_ids=[9],arms={'control':[0,1],'edit':[0,9]},
        observations=[dict(arm=arm,draw=i,continuation_ids=[1],seed=2*i+(arm=='edit'),stop_reason='eos',label='A') for i in range(2) for arm in ('control','edit')])
    return edit,capture


def test_v3_full_artifact_family_roundtrip_and_tamper(tmp_path,archive):
    from test_patch_bundle import patch as patch_fixture
    patch=patch_fixture.__wrapped__(archive)
    edit,capture=basic_artifacts(archive)
    bundle=build_bundle([archive],patches=[patch],edits=[edit],captures=[capture])
    job=bundle['payload']['investigation'];job['conclusion']='These are synthetic import fixtures.'
    branch=archive['records']['pass_1']['branches'][0]
    indices=[i for b in archive['records']['pass_1']['branches'] if b['t']==branch['t'] for i in b['draw_indices']]
    job['comparisons']=[dict(id='f'*32,run_id=archive['id'],pass_id='pass_1',checkpoint=branch['t'],draw_indices=indices[:2],rationale='Synthetic pair')]
    bundle['sha256']=digest(bundle['payload']);validate_bundle(bundle)
    import_bundle(bundle,tmp_path/'live-runs')
    assert import_bundle(bundle,tmp_path/'live-runs')['already_present']
    restored=json.loads((tmp_path/'workflow-jobs'/(job['id']+'.json')).read_text())
    assert restored['comparisons']==job['comparisons'] and restored['conclusion']==job['conclusion']
    for kind in ('edits','captures'):
        bad=copy.deepcopy(bundle);bad['payload'][kind][0]['source_ids_sha256']='fake';bad['sha256']=digest(bad['payload'])
        with pytest.raises(ValueError,match='checksum'):validate_bundle(bad)
    bad=copy.deepcopy(bundle);bad['payload']['captures'][0]['captures'][0]['prefix_ids']=[99];bad['sha256']=digest(bad['payload'])
    with pytest.raises(ValueError,match='prefix'):validate_bundle(bad)
    bad=copy.deepcopy(bundle);bad['payload']['investigation']['comparisons'][0]['draw_indices']=[999,1000];bad['sha256']=digest(bad['payload'])
    with pytest.raises(ValueError,match='missing'):validate_bundle(bad)


def test_direct_run_family_includes_durable_response_and_reexports(tmp_path,monkeypatch,archive):
    from fork_microscope import live_service
    from fork_microscope.investigation_bundle import export_family
    raw=archive['records']['pass_1']['base']
    model=SimpleNamespace(info=archive['model'],is_muse=False,decode=lambda ids:raw['base_text'],tokenizer=SimpleNamespace(decode=lambda ids,**kw:raw['base_text']))
    base=SimpleNamespace(prompt_ids=raw['prompt_ids'],gen_ids=raw['gen_ids'],finish_reason='stop')
    response=make_response(model,base,archive['base_config'],'d'*32)
    archive['source_response_id']=response['id']
    service=SimpleNamespace(results=lambda:[archive],result=lambda id,raw=False:copy.deepcopy(archive),investigations=lambda:[],response=lambda id:response)
    monkeypatch.setattr(live_service,'RUNS',tmp_path/'live-runs')
    bundle=export_family(service,archive['id'])
    assert bundle['schema']=='fork-investigation-bundle-v3'
    assert bundle['manifest']['response_ids']==[response['id']]
    import_bundle(bundle,live_service.RUNS)
    installed=live_service.LiveService(workspace_root=tmp_path/'workspace')
    again=export_family(installed,archive['id'])
    assert again['payload']['responses']==bundle['payload']['responses']
    assert again['payload']['runs']==bundle['payload']['runs']
    assert import_bundle(again,live_service.RUNS)['already_present']


def test_terminal_publication_releases_manual_owner_first(tmp_path,archive,monkeypatch):
    service,manager,d=setup(tmp_path,archive,[('Final answer: B','stop')])
    observed=[];save=manager.save
    def capture(record):
        if record['status']!='running':observed.append((record['status'],manager.active,service.workflow_owner))
        save(record)
    monkeypatch.setattr(manager,'save',capture)
    result,_=search(manager,d['id'],max_attempts=1)
    assert result['status']=='idle'
    assert observed==[('idle',None,None)]


def test_terminal_publication_releases_automatic_owner_first(tmp_path,archive,monkeypatch):
    from test_workflow import config as config_fixture
    service=FakeService(archive);manager=WorkflowManager(service,tmp_path)
    observed=[];save=manager.save
    def capture(record):
        if record['status']!='running':observed.append((record['status'],manager.active,service.workflow_owner))
        save(record)
    monkeypatch.setattr(manager,'save',capture)
    result=finished(manager,manager.start({'request_id':'owner','config':config_fixture.__wrapped__()})['id'])
    assert result['status']=='complete'
    assert observed==[('complete',None,None)]
