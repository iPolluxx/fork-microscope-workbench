"""Queue lifecycle with a deterministic fake worker; never loads weights."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from fork_microscope.live_service import LiveService, Cancelled
from fork_microscope.workspace_store import WorkspaceStore, scan_config

SCAN=dict(stride=32,samples=20,cont_max=64,temperature=1,top_k=10,threshold=.05,seed=9,tuning='cv')
def p(id='one',text='Choose X or Y.'):
    return dict(id=id,title=id,prompt=text,answers=['X','Y'],mode='chat',max_tokens=64,seed=0)
def save(store):return store.save_set(dict(name='Questions',prompts=[p(),p('two')]))

def test_prompt_validation_and_immutable_snapshot(tmp_path):
    store=WorkspaceStore(tmp_path);saved=save(store)
    batch=store.prepare_batch(dict(set_id=saved['id'],prompt_ids=['one'],scan=SCAN),{'model_id':'test'})
    store.save_set(dict(id=saved['id'],name='Edited',prompts=[p(text='Changed')]))
    assert batch['items'][0]['prompt_snapshot']['prompt']=='Choose X or Y.'
    assert store.list('sets')[0]['revision']==2
    store.delete_set(saved['id']);assert batch['set_name']=='Questions'
    with pytest.raises(ValueError):store.delete_set('../escape')
    with pytest.raises(ValueError):store.save_set(dict(name='test',prompts=[p(),p()]))
    with pytest.raises(ValueError):store.save_set(dict(name='test',prompts=[p()|{'answers':['X','X']}]))

def test_whole_trace_grid_includes_short_and_offgrid_endpoints():
    assert scan_config(SCAN,7)['passes'][0]['positions']==[0,7]
    assert scan_config(SCAN,66)['passes'][0]['positions']==[0,32,64,66]
    with pytest.raises(ValueError):scan_config(SCAN|{'samples':True},5)
    with pytest.raises(ValueError):scan_config(SCAN|{'temperature':float('nan')},5)

def test_restart_preserves_completed_and_marks_incomplete(tmp_path):
    store=WorkspaceStore(tmp_path);s=save(store)
    b=store.prepare_batch(dict(set_id=s['id'],prompt_ids=['one','two'],scan=SCAN),{})
    b['state']='running';b['items'][0].update(state='complete',run_id='a'*32);b['items'][1]['state']='running'
    store.save_batch(b);(tmp_path/'batches'/'broken.json').write_text('{')
    store.recover();restored=store.list('batches')[0]
    assert restored['state']=='interrupted' and restored['items'][0]['run_id']=='a'*32
    assert restored['items'][1]['state']=='interrupted'

def setup_batch(tmp_path, monkeypatch):
    service=LiveService(workspace_root=tmp_path);saved=save(service.workspace)
    service.model=SimpleNamespace(info={'model_id':'fake','resolved_revision':'fixed'})
    batch=service.workspace.prepare_batch(dict(set_id=saved['id'],prompt_ids=['one','two'],scan=SCAN),service.model.info)
    service.job=dict(id='f'*32,status='running');service.stop.clear()
    calls=[]
    def base(payload):service.base=SimpleNamespace(gen_ids=list(range(8 if len(calls)==0 else 67)))
    monkeypatch.setattr(service,'generate_base',base);monkeypatch.setattr(service,'estimate',lambda c:None)
    def collect(c,run_id=None):calls.append((deepcopy(c),run_id))
    monkeypatch.setattr(service,'collect',collect)
    return service,batch,calls

def test_batch_uses_each_trace_length_and_distinct_run_ids(tmp_path,monkeypatch):
    s,b,calls=setup_batch(tmp_path,monkeypatch);s.execute_batch(b)
    assert b['state']=='complete'
    assert [c[0]['passes'][0]['positions'] for c in calls]==[[0,7],[0,32,64,66]]
    assert len(set(c[1] for c in calls))==2
    assert all(i['state']=='complete' for i in s.workspace.list('batches')[0]['items'])

def test_stop_preserves_finished_and_cancels_pending(tmp_path,monkeypatch):
    s,b,calls=setup_batch(tmp_path,monkeypatch)
    def collect(c,run_id=None):calls.append(run_id);s.stop.set()
    monkeypatch.setattr(s,'collect',collect)
    with pytest.raises(Cancelled):s.execute_batch(b)
    assert b['state']=='cancelled'
    assert [i['state'] for i in b['items']]==['complete','cancelled']
    with pytest.raises(ValueError):s.cancel('wrong-job')

def test_one_item_failure_does_not_hide_other_results(tmp_path,monkeypatch):
    s,b,calls=setup_batch(tmp_path,monkeypatch)
    def collect(c,run_id=None):
        calls.append(run_id)
        if len(calls)==1:raise ValueError('fixture failure')
    monkeypatch.setattr(s,'collect',collect)
    with pytest.raises(ValueError,match='item errors'):s.execute_batch(b)
    assert b['state']=='complete_with_errors'
    assert [i['state'] for i in b['items']]==['error','complete']
