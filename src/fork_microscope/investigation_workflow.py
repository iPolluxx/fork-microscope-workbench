"""Single-owner, durable orchestration on an existing Fork Microscope worker.

Budgets reserve worst-case generated tokens before each operation. Interrupted
reservations are not refunded. Time cancellation is cooperative, not a billing cap.
"""
import copy
import json
import math
from pathlib import Path
import threading
import time
import uuid

from fork_microscope.investigation_bundle import digest, canonical, identifier, build_bundle

TERMINAL={'complete','cancelled','error','interrupted','budget_exhausted'}

def validate_config(c):
    if not isinstance(c,dict) or set(c)!={'model','base','scan','refinement','lens','limits'}:raise ValueError('Config requires model, base, scan, refinement, lens and limits.')
    if any(not isinstance(c[k],dict) for k in ('model','base','scan','refinement','limits')):raise ValueError('Model, base, scan, refinement and limits must be objects.')
    if c['lens'] is not None and not isinstance(c['lens'],dict):raise ValueError('Lens must be an object or null.')
    m=c['model'];b=c['base'];s=c['scan'];r=c['refinement'];limits=c['limits']
    if set(m)!={'model_id','revision','device','batch_size'} or not all(isinstance(m[k],str) and m[k] for k in ('model_id','revision')):raise ValueError('Specify model identity and pinned revision.')
    if m['revision'] in ('main','master','latest'):raise ValueError('Use a pinned model revision, not a moving branch.')
    if m['device'] not in ('auto','cpu','cuda'):raise ValueError('Invalid model device.')
    def whole(x,lo,hi,name):
        if type(x) is not int or not lo<=x<=hi:raise ValueError(f'{name} must be an integer between {lo} and {hi}.')
    whole(m['batch_size'],1,128,'Batch size')
    if set(b)!={'prompt','answers','mode','max_tokens','seed'} or not isinstance(b['prompt'],str) or not 1<=len(b['prompt'].strip())<=16000:raise ValueError('Invalid prompt settings.')
    from fork_microscope.outcome_readout import validate_answers
    validate_answers(b['answers'])
    if b['mode'] not in ('chat','base'):raise ValueError('Invalid prompt mode.')
    whole(b['max_tokens'],8,4096,'Base token cap');whole(b['seed'],0,2**31-1,'Seed')
    if set(s)!={'start','end','stride','samples','cont_max','temperature','top_k','threshold','seed'}:raise ValueError('Invalid scan settings.')
    whole(s['start'],0,4095,'Scan start')
    if s['end'] is not None:whole(s['end'],s['start']+1,4095,'Scan end')
    for key,lo,hi in [('stride',1,128),('samples',5,512),('cont_max',1,4096),('top_k',1,50),('seed',0,2**31-1)]:whole(s[key],lo,hi,key)
    for key,lo,hi in [('temperature',.05,2),('threshold',0,1)]:
        if type(s[key]) not in (int,float) or not math.isfinite(s[key]) or not lo<=s[key]<=hi:raise ValueError('Invalid '+key)
    if set(r)!={'max_rounds','stride','samples','min_tvd'}:raise ValueError('Invalid refinement policy.')
    whole(r['max_rounds'],0,8,'Refinement rounds');whole(r['stride'],1,128,'Refinement stride');whole(r['samples'],5,512,'Refinement samples')
    if type(r['min_tvd']) not in (int,float) or not math.isfinite(r['min_tvd']) or not 0<=r['min_tvd']<=1:raise ValueError('Invalid TVD threshold.')
    if set(limits)!={'max_seconds','max_samples','max_generated_tokens'}:raise ValueError('Supply time, sample and generated-token limits.')
    for k in limits:whole(limits[k],1,10**9,k)
    l=c['lens']
    if l is not None:
        fields={'profile','layers','before','after','top_k'}
        if set(l) not in (fields, fields | {'inspection_backend'}) or not isinstance(l['profile'],str):raise ValueError('Invalid lens settings.')
        if l.get('inspection_backend','native') not in ('native','nnsight'):raise ValueError('Lens inspection_backend must be native or nnsight.')
        if not isinstance(l['layers'],list) or not 1<=len(l['layers'])<=16 or any(type(x) is not int for x in l['layers']) or len(set(l['layers']))!=len(l['layers']):raise ValueError('Select unique lens layers.')
        for layer in l['layers']:whole(layer,0,999,'Lens layer')
        for k,lo,hi in [('before',0,16),('after',0,16),('top_k',2,30)]:whole(l[k],lo,hi,k)
    canonical(c)
    return copy.deepcopy(c)

def scan_request(config,last):
    s=config['scan'];end=min(last,s['end'] if s['end'] is not None else last)
    positions=list(range(s['start'],end+1,s['stride']))
    if len(positions)<2:raise ValueError('Completed response is too short for two configured checkpoints.')
    return dict(cont_max=s['cont_max'],temperature=s['temperature'],top_k=s['top_k'],threshold=s['threshold'],
        dense=False,reference_samples=5,tuning='cv',passes=[dict(id='scan',label='Initial scan',
        start=positions[0],end=positions[-1],stride=s['stride'],offset=0,samples=s['samples'],seed=s['seed'])])

def refinement_choice(run,policy,round_index):
    """Largest raw adjacent TVD among valid completed fits. Descriptive heuristic."""
    candidates=[]
    for p in run['passes']:
        c=p['curve'];pos=c['positions'];rows=c['weighted']
        if c.get('fit_status')!='complete':continue
        for i,(left,right) in enumerate(zip(pos,pos[1:])):
            tvd=sum(abs(a-b) for a,b in zip(rows[i],rows[i+1]))/2
            if right-left>policy['stride'] and tvd>=policy['min_tvd']:
                candidates.append((tvd,left,right,p['id']))
    if not candidates:return None
    tvd,left,right,pid=sorted(candidates,key=lambda x:(-x[0],x[1],x[3]))[0]
    return dict(source_run_id=run['id'],source_pass_id=pid,start=left,end=right,
                stride=policy['stride'],samples=policy['samples'],seed=(run['settings']['passes'][0]['seed']+round_index+1)%(2**31),cont_max=run['settings']['cont_max']),dict(
                policy='largest-adjacent-raw-tvd-v1',tvd=tvd,interval=[left,right],
                explanation='Post-selected descriptive difference; not a significance test or a validated savings policy.')

def lens_choice(run,settings):
    candidates=[]
    for p in run['passes']:
        rec=run['records'][p['id']];by_t={}
        for b in rec['branches']:
            for i,o in enumerate(b.get('observations',[])):
                if o.get('stop_reason') in (None,'','length') or o.get('label') in (None,'','Other'):continue
                ids=rec['base']['gen_ids'][:b['t']]+[b['tok_id']]+b['continuation_ids'][i]
                by_t.setdefault(b['t'],[]).append((b['draw_indices'][i],o['label'],ids))
        for t,draws in by_t.items():
            draws.sort();labels={d[1] for d in draws}
            if len(labels)<2:continue
            # Most balanced observed checkpoint; pair selected deterministically.
            balance=1-max(sum(d[1]==label for d in draws) for label in labels)/len(draws)
            a=draws[0];b=next(d for d in draws if d[1]!=a[1])
            diff=next((i for i,(x,y) in enumerate(zip(a[2],b[2])) if x!=y),None)
            if diff is None:continue
            candidates.append((balance,t,p['id'],a,b,diff))
    if not candidates:return None
    _,t,pid,a,b,diff=sorted(candidates,key=lambda x:(-x[0],x[1],x[2]))[0]
    start=max(0,diff-settings['before']);end=min(len(a[2]),len(b[2]),diff+settings['after']+1)-1
    req=dict(source_run_id=run['id'],source_pass_id=pid,lens={'profile':settings['profile']},
             selection=dict(type='draw_pair',checkpoint=t,draw_indices=[a[0],b[0]]),space='response',
             start=start,end=end,layers=settings['layers'],top_k=settings['top_k'])
    if 'inspection_backend' in settings: req['inspection_backend']=settings['inspection_backend']
    return req,dict(policy='balanced-checkpoint-first-contrasting-pair-v1',first_difference=diff,
        explanation='Post-selected outcomes; readouts are after each token, observational and not causal. Equal indices need not align semantically.')

class BudgetStop(Exception):pass
class UserStop(Exception):pass

class WorkflowManager:
    def __init__(self,service,root):
        self.service=service;service.workflow_manager=self;self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();self.active=None;self.cancelled=set()
        for p in self.root.glob('*.json'):
            try:
                d=json.loads(p.read_text())
                if d.get('status')=='running':
                    d['elapsed_seconds']=max(d.get('elapsed_seconds',0),d['config']['limits']['max_seconds']-max(0,d.get('deadline_at',time.time()+d['config']['limits']['max_seconds']-d.get('elapsed_seconds',0))-time.time()))
                    d.update(status='interrupted',message='Worker restarted. Completed artifacts are retained; resume explicitly.');self.save(d)
            except (OSError,ValueError):continue
    def save(self,d):
        with self.lock:
            if d['id'] in self.cancelled:d['cancellation_requested']=True
            p=self.root/(identifier(d['id'])+'.json');tmp=p.with_suffix('.tmp');tmp.write_bytes(canonical(d));tmp.replace(p)
    def read(self,id):
        p=self.root/(identifier(id)+'.json')
        if not p.exists():raise ValueError('Investigation job not found.')
        d=json.loads(p.read_text())
        if self.active==id:
            with self.service.lock:d['worker_job']=copy.deepcopy(self.service.job)
        return d
    def list(self):return [self.read(p.stem) for p in sorted(self.root.glob('*.json'),key=lambda x:x.stat().st_mtime,reverse=True)]
    def start(self,request):
        if not isinstance(request,dict) or set(request)!={'request_id','config'}:raise ValueError('Provide request_id and config.')
        key=request['request_id']
        if not isinstance(key,str) or not 1<=len(key)<=128:raise ValueError('Use a stable request ID, 1–128 characters.')
        config=validate_config(request['config']);id=digest(key)[:32]
        with self.lock:
            if (self.root/(id+'.json')).exists():
                d=self.read(id)
                if d['config']!=config:raise ValueError('Request ID already belongs to different settings.')
                return d
            d=dict(id=id,schema='fork-workflow-v1',created=time.time(),status='running',config=config,
                   runs=[],lenses=[],steps=[],reservations={'samples':0,'generated_tokens':0},elapsed_seconds=0,
                   cancellation_requested=False,message='Starting investigation')
            self.launch(d);return self.read(id)
    def launch(self,d):
        with self.service.lock:
            if self.active or self.service.job['status']=='running':raise ValueError('Worker is busy; retry later with the same request ID.')
            self.active=d['id'];self.service.workflow_owner=d['id'];self.save(d)
        threading.Thread(target=self.execute,args=(d,),daemon=True).start()
    def cancel(self,id):
        with self.lock:
            d=self.read(id)
            if d['status'] in TERMINAL:return d
            self.cancelled.add(id);d.pop('worker_job',None);d['cancellation_requested']=True;self.save(d)
            if self.active==id:self.service.cancel()
            return d
    def resume(self,id):
        with self.lock:
            d=self.read(id)
            if d['status'] not in ('interrupted','error'):raise ValueError('Only interrupted or failed jobs can resume. Cancelled or exhausted jobs need a new request.')
            # Do not repeat a possibly completed paid phase. Reconcile its stable ID.
            pending=d.get('pending')
            if pending:
                if pending['action'] in ('run','refine'):
                    try:
                        self.service.result(pending['job_id'])
                        if pending['job_id'] not in d['runs']:d['runs'].append(pending['job_id'])
                    except ValueError:pass
                elif pending['action']=='lens':
                    try:
                        l=self.service.investigation(pending['job_id'])
                        if l['status']=='complete' and l['id'] not in d['lenses']:d['lenses'].append(l['id'])
                    except ValueError:pass
                d['steps'].append(dict(event='resume',pending=pending,note='Unfinished phase may rerun; its reservation remains charged.'))
                d.pop('pending',None)
            d.update(status='running',cancellation_requested=False,message='Resuming from saved evidence')
            self.launch(d);return self.read(id)
    def checkpoint(self,d):
        with self.lock:
            latest=self.read(d['id'])
            if d['id'] in self.cancelled or latest.get('cancellation_requested'):raise UserStop()
        if time.monotonic()>=self.deadline:raise BudgetStop('Time allowance reached at a cooperative cancellation boundary.')
    def reserve(self,d,samples=0,tokens=0):
        limits=d['config']['limits'];r=d['reservations']
        if r['samples']+samples>limits['max_samples'] or r['generated_tokens']+tokens>limits['max_generated_tokens']:
            raise BudgetStop('Next operation exceeds the remaining worst-case sample/token allowance.')
        r['samples']+=samples;r['generated_tokens']+=tokens
    def perform(self,d,action,payload,samples=0,tokens=0,rationale=None):
        self.checkpoint(d);self.reserve(d,samples,tokens)
        # Persist an operation ID BEFORE it can start. Reconcile by this ID on restart.
        operation=uuid.uuid4().hex;d['pending']=dict(job_id=operation,action=action,payload=payload)
        d['steps'].append(dict(action=action,job_id=operation,rationale=rationale,reserved_samples=samples,reserved_tokens=tokens))
        d['message']='Running '+action;self.save(d)
        self.service.start(action,payload,owner=d['id'],job_id=operation)
        while True:
            try:self.checkpoint(d)
            except (BudgetStop,UserStop):
                self.service.cancel(operation)
                # Keep ownership until the model has actually stopped.
                while self.service.job['status']=='running':time.sleep(.1)
                raise
            with self.service.lock:job=dict(self.service.job)
            if job['status']!='running':break
            time.sleep(.1)
        if job['status']!='complete':raise RuntimeError(f"{action} did not complete: {job.get('phase','unknown error')}")
        if action in ('run','refine'):d['runs'].append(job['result_id'])
        if action=='lens':d['lenses'].append(job['investigation_id'])
        d.pop('pending',None);self.save(d)
        return job
    def execute(self,d):
        started=time.monotonic();c=d['config'];remaining=c['limits']['max_seconds']-d['elapsed_seconds'];self.deadline=started+max(0,remaining)
        self.service.workflow_deadline=self.deadline
        d['deadline_at']=time.time()+max(0,remaining);self.save(d)
        try:
            self.checkpoint(d)
            optional_nnsight = bool(c['lens'] and not d['lenses'] and c['lens'].get('inspection_backend','native') == 'nnsight')
            if optional_nnsight:
                # Fail before loading a model or spending scan samples. Syntax
                # validation stays offline; execution verifies the real worker.
                from fork_microscope.nnsight_inspection import require
                require(None)
            from fork_microscope.model_preflight import same_model_identity
            wanted={'model_id':c['model']['model_id'],'resolved_revision':c['model']['revision']}
            if not self.service.model or not same_model_identity(self.service.model.info,wanted):self.perform(d,'load',c['model'])
            if optional_nnsight: require(self.service.model)
            if not d['runs']:
                self.perform(d,'base',c['base'],tokens=c['base']['max_tokens'])
                scan=scan_request(c,len(self.service.base.gen_ids)-1)
                estimate=self.service.estimate(scan)
                self.perform(d,'run',scan,estimate['total_rollouts'],estimate['max_continuation_tokens'])
            while len(d['runs'])-1<c['refinement']['max_rounds']:
                run=self.service.result(d['runs'][-1],raw=True);choice=refinement_choice(run,c['refinement'],len(d['runs']))
                if not choice:
                    d['steps'].append(dict(action='refinement_skipped',reason='No eligible adjacent interval exceeds the configured descriptive threshold.'));break
                req,why=choice;plan=self.service.refinement_plan(req);p=plan['run']['passes'][0]
                n=len(p['positions'])*p['samples']
                self.perform(d,'refine',req,n,n*plan['run']['cont_max'],why)
            if c['lens'] and not d['lenses']:
                choice=None
                for id in reversed(d['runs']):
                    choice=lens_choice(self.service.result(id,raw=True),c['lens'])
                    if choice:break
                if choice:
                    req,why=choice;self.perform(d,'lens',req,rationale=why)
                else:d['steps'].append(dict(action='lens_skipped',reason='No completed, classified contrasting pair was found.'))
            d.update(status='complete',message='Investigation complete; no fork or causal explanation is guaranteed.')
        except BudgetStop as exc:d.update(status='budget_exhausted',message=str(exc))
        except UserStop:d.update(status='cancelled',message='Cancelled; completed evidence is retained.')
        except Exception as exc:
            if time.monotonic()>=self.deadline:d.update(status='budget_exhausted',message='Time allowance reached; completed evidence is retained.')
            else:d.update(status='error',message=str(exc)[:1500])
        finally:
            d['elapsed_seconds']+=time.monotonic()-started
            with self.lock:
                # Preserve a cancellation requested while a phase finished.
                latest=self.read(d['id'])
                if d['id'] in self.cancelled:d.update(status='cancelled',message='Cancelled at completion boundary.')
                self.save(d)
                with self.service.lock:self.service.workflow_owner=None;self.service.workflow_deadline=None
                self.active=None
    def export(self,id):
        d=self.read(id)
        if not d['runs']:raise ValueError('No completed runs are available to export yet.')
        return build_bundle([self.service.result(i,raw=True) for i in d['runs']],
                            [self.service.investigation(i) for i in d['lenses']],d)
