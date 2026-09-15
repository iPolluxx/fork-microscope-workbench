"""Portable refinement plans: lineage, exact endpoints, unchanged sampling defaults."""
import copy
import hashlib
import json
from fork_microscope.sampling import pass_plan


def build_plan(result, record, request):
    required={'source_run_id','source_pass_id','start','end','stride','samples','seed','cont_max'}
    if type(request) is not dict or set(request)!=required:raise ValueError('Invalid refinement request fields.')
    if request['source_run_id']!=result['id']:raise ValueError('Source run mismatch.')
    if request['source_pass_id'] not in [p['id'] for p in result['passes']]:raise ValueError('Unknown source pass.')
    ids=record['base']['gen_ids']
    for key,lo,hi in [('start',0,len(ids)-1),('end',0,len(ids)-1),('stride',1,128),('samples',5,512),('seed',0,2**31-1),('cont_max',1,4096)]:
        if type(request[key]) is not int or not lo<=request[key]<=hi:raise ValueError('Invalid '+key)
    if request['end']<=request['start']:raise ValueError('End must follow start.')
    positions=list(range(request['start'],request['end']+1,request['stride']))
    if positions[-1]!=request['end']:positions.append(request['end'])
    settings=copy.deepcopy(result['settings'])
    # Modern explicit pass format; do not inherit a reference or legacy grid.
    settings={k:settings[k] for k in ['temperature','top_k','threshold','reference_samples','tuning']}
    settings.update(dense=False,cont_max=request['cont_max'],passes=[dict(id='refinement',label=f"Refinement {request['start']}–{request['end']}",start=request['start'],end=request['end'],stride=request['stride'],offset=0,samples=request['samples'],seed=request['seed'],positions=positions)])
    pass_plan(settings,len(ids)-1)
    revision=result['model'].get('resolved_revision')
    if not revision:raise ValueError('Source has no resolved model revision.')
    total=len(positions)*request['samples']
    return dict(schema='fork-refinement-v1',request=copy.deepcopy(request),model=copy.deepcopy(result['model']),base=copy.deepcopy(record['base']),base_config=copy.deepcopy(result['base_config']),run=settings,
        lineage=dict(source_run_id=result['id'],source_pass_id=request['source_pass_id'],source_cont_max=result['settings']['cont_max'],source_ids_sha256=hashlib.sha256(json.dumps({'prompt_ids':record['base']['prompt_ids'],'gen_ids':ids},sort_keys=True).encode()).hexdigest(),interval=[request['start'],request['end']],selected_after_inspecting_source=True),
        source_positions=[p for p in record.get('positions',[]) if p['t'] in positions],summary=dict(checkpoints=len(positions),continuations=total,max_new_tokens=total*request['cont_max'],cap_changed=request['cont_max']!=result['settings']['cont_max']))


def run_bundle(path):
    """Explicit CLI execution on a GPU worker; source files need not already exist there."""
    from pathlib import Path
    import time
    import uuid
    import threading
    from fork_microscope.live_service import LiveService
    bundle=json.loads(Path(path).read_text())
    if bundle.get('schema')!='fork-refinement-v1':raise ValueError('Unsupported refinement bundle.')
    request=bundle['request']
    # Rebuild the plan with normal validation instead of trusting editable summary fields.
    settings=copy.deepcopy(bundle['run']);settings['cont_max']=bundle['lineage']['source_cont_max']
    result=dict(id=request['source_run_id'],passes=[{'id':request['source_pass_id']}],settings=settings,model=bundle['model'],base_config=bundle['base_config'])
    plan=build_plan(result,dict(base=bundle['base'],positions=bundle.get('source_positions',[])),request)
    if plan['lineage']['source_ids_sha256']!=bundle['lineage']['source_ids_sha256']:raise ValueError('Source token checksum mismatch.')
    service=LiveService()
    def wait():
        previous=None
        while True:
            job=service.status()['job']
            if job!=previous:print(json.dumps(job),flush=True);previous=job
            if job['status']!='running':
                if job['status']!='complete':raise RuntimeError(job['phase'])
                return job
            time.sleep(2)
    service.start('load',dict(model_id=plan['model']['model_id'],revision=plan['model']['resolved_revision'],device='cuda',batch_size=1));wait()
    service.job=dict(id=uuid.uuid4().hex,status='running',action='refine',phase='Restoring saved trace',completed=0,total=0,started=time.time())
    threading.Thread(target=service._execute,args=('refine',plan),daemon=True).start()
    print(json.dumps({'completed_refinement':wait()['result_id']}),flush=True)
