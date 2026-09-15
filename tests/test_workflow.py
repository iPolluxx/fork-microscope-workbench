"""CPU-only orchestration and portability tests. No real inference or GPU calls."""
import copy
import json
import threading
import time
from types import SimpleNamespace
import pytest

from test_evidence_io import archive
from fork_microscope.investigation_bundle import build_bundle,validate_bundle,import_bundle,digest
from fork_microscope.investigation_workflow import WorkflowManager,validate_config,refinement_choice,lens_choice

@pytest.fixture
def config():
    return dict(model=dict(model_id='test/model',revision='test-revision',device='cpu',batch_size=1),
        base=dict(prompt='test',answers=['A','B','C','D'],mode='chat',max_tokens=16,seed=0),
        scan=dict(start=0,end=12,stride=4,samples=5,cont_max=10,temperature=1,top_k=5,threshold=.05,seed=0),
        refinement=dict(max_rounds=1,stride=2,samples=5,min_tvd=.2),lens=None,
        limits=dict(max_seconds=60,max_samples=100,max_generated_tokens=2000))

class FakeService:
    def __init__(self,archive,delay=0):
        self.archive=archive;self.lock=threading.RLock();self.job={'status':'idle'};self.model=None
        self.workflow_owner=None;self.workflow_deadline=None;self.calls=[];self.saved={};self.delay=delay
    def start(self,action,payload,owner=None,job_id=None):
        assert owner==self.workflow_owner
        self.calls.append(action);self.job={'id':job_id,'status':'running','phase':action}
        def finish():
            time.sleep(self.delay)
            if self.job['status']!='running':return
            if action=='load':self.model=SimpleNamespace(info={'model_id':payload['model_id'],'resolved_revision':payload['revision']})
            if action=='lens':self.job['investigation_id']=job_id
            if action=='base':self.base=SimpleNamespace(gen_ids=list(range(13)))
            if action in ('run','refine'):
                r=copy.deepcopy(self.archive);r['id']=job_id;self.saved[job_id]=r;self.job['result_id']=job_id
            self.job['status']='complete'
        threading.Thread(target=finish,daemon=True).start()
    def estimate(self,c):return {'total_rollouts':20,'max_continuation_tokens':200}
    def result(self,id,raw=False):
        if id not in self.saved:raise ValueError('not saved')
        return copy.deepcopy(self.saved[id])
    def cancel(self,id=None):
        if id is not None:assert self.job['id']==id
        self.job['status']='cancelled'
    def investigation(self,id):raise ValueError('not saved')
    def refinement_plan(self,req):
        from fork_microscope.refinement import build_plan
        r=self.result(req['source_run_id']);return build_plan(r,r['records'][req['source_pass_id']],req)


def finished(manager,id):
    end=time.monotonic()+5
    while time.monotonic()<end:
        d=manager.read(id)
        if d['status']!='running':return d
        time.sleep(.02)
    pytest.fail('Coordinator did not finish')

def test_end_to_end_idempotency_and_budget_accounting(tmp_path,archive,config):
    s=FakeService(archive);m=WorkflowManager(s,tmp_path)
    request={'request_id':'test','config':config};job=m.start(request);d=finished(m,job['id'])
    assert d['status']=='complete' and len(d['runs'])==1
    assert s.calls==['load','base','run'] and s.workflow_owner is None
    assert d['reservations']=={'samples':20,'generated_tokens':216}
    m.start(request);assert s.calls==['load','base','run']
    changed=copy.deepcopy(request);changed['config']['base']['prompt']='different'
    with pytest.raises(ValueError,match='different'):m.start(changed)
    bundle=m.export(d['id']);validate_bundle(bundle)
    assert bundle['manifest']['run_ids']==d['runs']

def test_budget_stops_before_sampling(tmp_path,archive,config):
    config['limits']['max_samples']=10
    s=FakeService(archive);m=WorkflowManager(s,tmp_path);d=finished(m,m.start({'request_id':'cap','config':config})['id'])
    assert d['status']=='budget_exhausted' and s.calls==['load','base']

def test_cancel_and_busy_guard(tmp_path,archive,config):
    s=FakeService(archive,delay=.5);m=WorkflowManager(s,tmp_path);id=m.start({'request_id':'cancel','config':config})['id']
    with pytest.raises(ValueError,match='busy'):m.start({'request_id':'other','config':config})
    m.cancel(id);d=finished(m,id)
    assert d['status']=='cancelled' and not d['runs'] and s.workflow_owner is None
    assert m.cancel(id)['status']=='cancelled'

def test_restart_marks_interrupted_and_reconciles_completed_run(tmp_path,archive,config):
    s=FakeService(archive);m=WorkflowManager(s,tmp_path);id='c'*32;run='d'*32
    r=copy.deepcopy(archive);r['id']=run;s.saved[run]=r
    m.save(dict(id=id,schema='fork-workflow-v1',status='running',config=config,created=1,runs=[],lenses=[],steps=[],
                reservations={'samples':20,'generated_tokens':216},elapsed_seconds=1,cancellation_requested=False,
                pending={'action':'run','job_id':run,'payload':{}}))
    m=WorkflowManager(s,tmp_path);assert m.read(id)['status']=='interrupted'
    d=finished(m,m.resume(id)['id']);assert d['runs']==[run] and 'run' not in s.calls and 'base' not in s.calls


def test_refinement_is_explicit_descriptive_and_budgetable(archive):
    r=copy.deepcopy(archive);c=r['passes'][0]['curve'];c['fit_status']='complete';c['weighted'][1]=[0,1,0,0,0]
    req,why=refinement_choice(r,{'stride':3,'samples':20,'min_tvd':.3},1)
    assert (req['start'],req['end'])==(0,4) and why['tvd']==pytest.approx(.6)
    assert 'not a significance test' in why['explanation']
    c['fit_status']='withheld';assert refinement_choice(r,{'stride':1,'min_tvd':0},1) is None

def test_bundle_roundtrip_and_collision_are_safe(tmp_path,archive):
    b=build_bundle([archive]);result=import_bundle(b,tmp_path/'live-runs');assert not result['already_present']
    assert import_bundle(b,tmp_path/'live-runs')['already_present']
    other=copy.deepcopy(archive);other['created']=2
    with pytest.raises(ValueError,match='Conflicting'):import_bundle(build_bundle([other]),tmp_path/'live-runs')
    assert json.loads((tmp_path/'live-runs'/archive['id']/'result.json').read_text())['created']==1

@pytest.mark.parametrize('defect',['checksum','parent','cycle','id','lens','missing','nonfinite'])
def test_reject_bad_bundles_before_writes(tmp_path,archive,defect):
    b=build_bundle([archive]);r=b['payload']['runs'][0]
    if defect=='checksum':b['sha256']='bad'
    if defect=='parent':r['lineage']={'source_run_id':'b'*32}
    if defect=='cycle':r['lineage']={'source_run_id':r['id']}
    if defect=='id':r['id']='../../escape'
    if defect=='lens':b['payload']['lenses']=[{'id':'b'*32,'schema':'fork-lens-v1','status':'complete','request':{'source_run_id':'b'*32}}]
    if defect=='missing':r.pop('records')
    if defect=='nonfinite':r['created']=float('nan')
    if defect not in ('checksum','nonfinite'):b['sha256']=digest(b['payload'])
    with pytest.raises(ValueError):import_bundle(b,tmp_path/'live-runs')
    assert not list(tmp_path.iterdir())

def test_sanitization_preserves_evidence_text(archive):
    r=copy.deepcopy(archive);r['model']['loading']={'hub_cache':'/private/home/cache'};r['authorization']='secret'
    r['base_config']['prompt']='The word token is evidence, not a credential.'
    b=build_bundle([r]);raw=json.dumps(b)
    assert '/private/home' not in raw and 'secret' not in raw and 'The word token' in raw


def test_worker_rejects_manual_mutation_during_workflow():
    from fork_microscope.live_service import LiveService
    s=LiveService();s.workflow_owner='owned'
    with pytest.raises(ValueError,match='owns'):s.start('unload',{})


def test_cli_blocks_credential_redirects_and_remote_plaintext():
    from fork_microscope.workflow_cli import Client
    with pytest.raises(ValueError):Client('http://gpu.example.org')
    with pytest.raises(ValueError):Client('https://user:pass@gpu.example.org')
    assert Client('http://127.0.0.1:8767').url.endswith('8767')


def test_refinement_reserves_nondivisible_endpoint(tmp_path,archive,config):
    archive['passes'][0]['curve'].update(fit_status='complete')
    archive['passes'][0]['curve']['weighted'][1]=[0,1,0,0,0]
    config['refinement'].update(stride=3,samples=5)
    s=FakeService(archive);m=WorkflowManager(s,tmp_path)
    d=finished(m,m.start({'request_id':'refine','config':config})['id'])
    assert d['status']=='complete' and s.calls==['load','base','run','refine']
    assert d['reservations']['samples']==35 # initial 20 + [0,3,4] x 5
    assert len(d['runs'])==2 and d['steps'][-1]['rationale']['interval']==[0,4]


def test_optional_lens_without_pair_is_valid_completion(tmp_path,archive,config):
    config['lens']=dict(profile='muse_glimmer',layers=[12],before=1,after=2,top_k=5)
    for b in archive['records']['pass_1']['branches']:
        for o in b['observations']:o['label']='A';o['stop_reason']='stop'
    s=FakeService(archive);m=WorkflowManager(s,tmp_path);d=finished(m,m.start({'request_id':'same','config':config})['id'])
    assert d['status']=='complete' and not d['lenses'] and d['steps'][-1]['action']=='lens_skipped'


def test_paired_lens_is_executed_when_outcomes_differ(tmp_path,archive,config):
    config['lens']=dict(profile='muse_glimmer',layers=[12],before=1,after=2,top_k=5)
    for b in archive['records']['pass_1']['branches']:
        for o in b['observations']:o['stop_reason']='stop'
    # The fake fixture can have different outcome labels on identical token paths;
    # supply a genuine token difference to test pair selection.
    for b in archive['records']['pass_1']['branches']:
        for i,o in enumerate(b['observations']):b['continuation_ids'][i]=[10 if o['label']=='A' else 20,30]
    s=FakeService(archive);m=WorkflowManager(s,tmp_path);d=finished(m,m.start({'request_id':'pair','config':config})['id'])
    assert d['status']=='complete' and len(d['lenses'])==1 and s.calls[-1]=='lens'
    assert d['steps'][-1]['rationale']['first_difference']>=0


def test_expired_job_starts_no_model_work(tmp_path,archive,config):
    s=FakeService(archive);m=WorkflowManager(s,tmp_path);id='e'*32
    m.save(dict(id=id,status='interrupted',config=config,created=1,runs=[],lenses=[],steps=[],
                reservations={'samples':0,'generated_tokens':0},elapsed_seconds=61,cancellation_requested=False))
    d=finished(m,m.resume(id)['id']);assert d['status']=='budget_exhausted' and not s.calls


def test_authenticated_http_client_roundtrip(tmp_path,archive,config,monkeypatch):
    from http.server import ThreadingHTTPServer
    from fork_microscope import microscope_server
    from fork_microscope.worker_connection import WorkerAccess
    from fork_microscope.workflow_cli import Client
    s=FakeService(archive);m=WorkflowManager(s,tmp_path/'jobs')
    monkeypatch.setattr(microscope_server,'WORKFLOWS',m)
    server=ThreadingHTTPServer(('127.0.0.1',0),microscope_server.Handler)
    server.worker_access=WorkerAccess('127.0.0.1','x'*40)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}'
    try:
        client=Client(url,token='x'*40)
        with pytest.raises(ValueError,match='access token'):Client(url,token='wrong').request('workflows')
        request={'request_id':'api-test','config':config}
        first=client.request('workflow-start',request)
        second=client.request('workflow-start',request);assert first['id']==second['id']
        done=finished(m,first['id']);assert done['status']=='complete'
        assert client.request('workflow?id='+first['id'])['runs']==done['runs']
        out=tmp_path/'bundle.json';client.export('workflow-export?id='+first['id'],out)
        bundle=json.loads(out.read_text());validate_bundle(bundle)
        assert import_bundle(bundle,tmp_path/'destination'/'live-runs')['run_ids']==done['runs']
    finally:server.shutdown();server.server_close()


def test_settings_reference_covers_every_automatic_field(config):
    from fork_microscope.workflow_cli import settings_reference
    config['lens']=dict(profile='muse_glimmer',layers=[12],before=1,after=5,top_k=10)
    reference=settings_reference()
    expected={group+'.'+field for group,fields in config.items() for field in fields} | {'lens.inspection_backend'}
    actual=[row['path'] for row in reference['fields']]
    assert set(actual)==expected and len(actual)==len(expected)
    assert all(row['effect'] and row['allowed'] for row in reference['fields'])


def test_offline_settings_and_validation_never_create_a_client(tmp_path,config,monkeypatch,capsys):
    from fork_microscope import workflow_cli
    def forbidden(*args,**kwargs):pytest.fail('Offline help must not construct a worker client')
    monkeypatch.setattr(workflow_cli,'Client',forbidden)
    workflow_cli.execute(SimpleNamespace(operation='settings'))
    assert json.loads(capsys.readouterr().out)['schema']=='fork-workflow-settings-reference-v1'
    p=tmp_path/'config.json';p.write_text(json.dumps(config))
    workflow_cli.execute(SimpleNamespace(operation='validate',config=str(p)))
    assert json.loads(capsys.readouterr().out)['valid'] is True
    config['scan']['samples']=0;p.write_text(json.dumps(config))
    with pytest.raises(ValueError):workflow_cli.execute(SimpleNamespace(operation='validate',config=str(p)))


def test_bundle_checksum_survives_browser_number_formatting(tmp_path, archive):
    from fork_microscope.investigation_bundle import normalized_numbers
    # JSON.parse/stringify drops .0, including scores and metadata timestamps.
    b=build_bundle([archive],investigation={'elapsed_seconds':12.0})
    browser_value=normalized_numbers(copy.deepcopy(b))
    assert isinstance(browser_value['payload']['investigation']['elapsed_seconds'],int)
    validate_bundle(browser_value)
    import_bundle(browser_value,tmp_path/'runs')
    browser_value['payload']['investigation']['elapsed_seconds']=13
    with pytest.raises(ValueError,match='checksum'):validate_bundle(browser_value)


def test_original_v1_checksum_remains_importable(archive):
    from fork_microscope.investigation_bundle import legacy_digest
    b=build_bundle([archive],investigation={'elapsed_seconds':12.0})
    b['sha256']=legacy_digest(b['payload'])
    validate_bundle(b)


def test_checksum_preserves_large_integer_precision():
    assert digest({'n':2**53+1})!=digest({'n':2**53})
    assert digest({'n':1.0,'z':-0.0})==digest({'n':1,'z':0})
