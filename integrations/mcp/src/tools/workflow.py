"""Private adapters for the pinned Fork Microscope automatic workflow API."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Annotated

from fastmcp import FastMCP
from fork_microscope.workflow_cli import Client,settings_reference
from fork_microscope.investigation_workflow import validate_config
from fork_microscope.investigation_bundle import digest,identifier
from .profile import load_profile,private,write_private

REFERENCE='https://github.com/iPolluxx/fork-microscope-workbench/blob/5c0231c3d64bb8f167e41fea776e4ff2e578d048/src/fork_microscope/workflow_cli.py'
workflow_mcp=FastMCP(name='workflow')

def result(message,**data):return dict(message=message,reference=REFERENCE,artifacts=[],**data)

def redact(value,token):
    if isinstance(value,str):return value.replace(token,'[redacted]') if token else value
    if isinstance(value,list):return [redact(x,token) for x in value]
    if isinstance(value,dict):return {k:redact(v,token) for k,v in value.items()}
    return value

def worker(p):
    if not p['worker_url'] or not p['worker_token_file']:raise ValueError('No worker configured for this private profile. Use configure.py worker.')
    token=p['worker_token_file'].read_text().strip()
    if not token or '\n' in token or '\r' in token:raise ValueError('Configured token file must contain one nonempty token.')
    return Client(p['worker_url'],token=token),token

@contextmanager
def ledger(p):
    lock=p['state_dir']/'ledger.lock'
    fd=os.open(lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        private(lock);fcntl.flock(fd,fcntl.LOCK_EX)
        path=p['state_dir']/'ledger.json'
        if path.exists():private(path);d=json.loads(path.read_text())
        else:d={'profile_id':p['profile_id'],'jobs':{}}
        if d.get('profile_id')!=p['profile_id'] or not isinstance(d.get('jobs'),dict):raise ValueError('Ledger does not belong to this profile.')
        yield d,path
    finally:fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)

def owned(d,p,id):
    identifier(id)
    record=d['jobs'].get(id)
    if not record or record.get('worker_url')!=p['worker_url']:raise ValueError('This workflow is not owned by this profile on the configured worker.')
    return record

def call(client,token,path,payload=None):
    try:return client.request(path,payload)
    except ValueError as exc:raise ValueError(redact(str(exc),token)) from None
    except Exception:raise ValueError('Worker request failed. Check the configured connection; retry with the same request ID after an uncertain start.') from None

def compact(value,token):
    fields=('id','schema','status','message','cancellation_requested','created','elapsed_seconds','reservations','runs','lenses','responses','patches','edits','captures')
    data={k:value[k] for k in fields if k in value}
    for k,v in list(data.items()):
        if isinstance(v,list):data[k]=v[:100];data[k+'_total']=len(v)
        elif isinstance(v,str):data[k]=v[:2000]
    if isinstance(value.get('worker_job'),dict):data['worker_job']={k:value['worker_job'][k] for k in ('id','status','phase','progress') if k in value['worker_job'] and isinstance(value['worker_job'][k],(str,int,float,bool,type(None)))}
    return redact(data,token)

def capped(config,p):
    c=validate_config(config)
    if any(c['limits'][k]>v for k,v in p['limits'].items()):raise ValueError('Requested resource limits exceed this profile’s configured ceilings.')
    return c

@workflow_mcp.tool(annotations={'readOnlyHint':True})
def fork_microscope_workflow_settings(config: Annotated[dict | None,'Optional complete source workflow configuration to validate offline.']=None)->dict:
    """Explain supported automatic workflow settings and optionally validate a configuration.
    Optional config → source settings, private profile ceilings and owned workflow identifiers; no network.
    """
    p=load_profile()
    with ledger(p) as (d,path):ids=[id for id,row in d['jobs'].items() if row.get('worker_url')==p['worker_url']]
    data=result('Configuration help; syntax validation does not establish model or lens readiness.',settings=settings_reference(),limits=p['limits'],worker_configured=bool(p['worker_url']),owned_workflow_ids=ids[:100],owned_workflow_count=len(ids))
    if config is not None:data['validated_config']=capped(config,p)
    return data

@workflow_mcp.tool(annotations={'readOnlyHint':False,'destructiveHint':False,'idempotentHint':True})
def fork_microscope_start_workflow(config: Annotated[dict,'Complete source workflow config including explicit time, sample and generated-token limits.'],request_id: Annotated[str,'Stable client request ID; reuse unchanged on retries, choose a new ID for a new experiment.'],confirm_execution: Annotated[bool,'True only after the user authorizes execution on their configured compute.']=False)->dict:
    """Start bounded scan, adaptive refinement and optional J-lens on configured compute.
    Config and stable request ID → private owned job; confirmation permits potentially billable execution.
    """
    if not confirm_execution:raise ValueError('Set confirm_execution=true only with user authorization to run compute.')
    if not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('Use a stable request ID of 1–128 characters.')
    p=load_profile();c=capped(config,p);client,token=worker(p)
    key=digest({'profile_id':p['profile_id'],'worker_url':p['worker_url'],'request_id':request_id});id=digest(key)[:32]
    with ledger(p) as (d,path):
        record=d['jobs'].get(id)
        if record and record['config_hash']!=digest(c):raise ValueError('Request ID already belongs to different settings.')
        if not record:
            d['jobs'][id]={'worker_url':p['worker_url'],'request_key':key,'config_hash':digest(c),'limits':c['limits'],'status':'pending'};write_private(path,d)
        value=call(client,token,'workflow-start',{'request_id':key,'config':c})
        if value.get('id')!=id:raise ValueError('Worker returned an unexpected workflow identity.')
        d['jobs'][id]['status']=value.get('status');write_private(path,d)
    return result('Workflow submitted. Time stopping is cooperative, not a provider billing cap.',workflow=compact(value,token))

@workflow_mcp.tool(annotations={'readOnlyHint':True})
def fork_microscope_workflow_status(workflow_id: Annotated[str,'Workflow ID owned by this configured profile.'])->dict:
    """Read progress of a workflow created by this private profile.
    Owned workflow ID → bounded saved status and artifact identifiers.
    """
    return operation(workflow_id,'status')

def operation(id,action):
    p=load_profile()
    with ledger(p) as (d,path):
        record=owned(d,p,id)
        if action=='resume' and any(record.get('limits',{}).get(k,10**9)>v for k,v in p['limits'].items()):raise ValueError('Saved workflow limits exceed current profile ceilings; resume is blocked.')
        client,token=worker(p)
        value=call(client,token,'workflow?id='+id) if action=='status' else call(client,token,'workflow-'+action,{'id':id})
        if value.get('id')!=id:raise ValueError('Worker returned an unexpected workflow identity.')
    return result('Saved workflow state; cancellation and time limits are cooperative.',workflow=compact(value,token))

@workflow_mcp.tool(annotations={'readOnlyHint':False,'idempotentHint':True})
def fork_microscope_cancel_workflow(workflow_id: Annotated[str,'Workflow ID owned by this configured profile.'])->dict:
    """Request cooperative cancellation of an owned workflow.
    Owned workflow ID → source cancellation state; in-flight work may finish before stopping.
    """
    return operation(workflow_id,'cancel')

@workflow_mcp.tool(annotations={'readOnlyHint':False,'idempotentHint':False})
def fork_microscope_resume_workflow(workflow_id: Annotated[str,'Owned interrupted or failed workflow eligible for source recovery.'],confirm_execution: Annotated[bool,'True only with authorization to resume potentially billable compute.']=False)->dict:
    """Resume eligible interrupted or failed work using the source recovery implementation.
    Owned workflow ID and execution confirmation → resumed state or source eligibility error.
    """
    if not confirm_execution:raise ValueError('Set confirm_execution=true only with user authorization to resume compute.')
    return operation(workflow_id,'resume')

@workflow_mcp.tool(annotations={'readOnlyHint':False,'destructiveHint':False})
def fork_microscope_export_workflow(workflow_id: Annotated[str,'Owned workflow with saved evidence to export.'])->dict:
    """Export a worker investigation into this profile’s private evidence library.
    Owned workflow ID → validated native bundle, saved bundle ID and portable artifact path.
    """
    p=load_profile()
    with ledger(p) as (d,path):
        owned(d,p,workflow_id);client,token=worker(p)
        folder=Path(tempfile.mkdtemp(prefix='workflow-',dir=p['export_dir']));artifact=folder/'investigation.json'
        dest=None;installed=False
        try:
            meta=client.export('workflow-export?id='+workflow_id,artifact)
            bundle=json.loads(artifact.read_text())
            if bundle.get('payload',{}).get('investigation',{}).get('id')!=workflow_id:
                raise ValueError('Worker export does not match the owned workflow identity.')
            os.chmod(artifact,0o600)
            name=folder.name+'.json';dest=p['evidence_dir']/name
            fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);installed=True
            with os.fdopen(fd,'wb') as f:f.write(artifact.read_bytes())
        except Exception as exc:
            if installed:dest.unlink(missing_ok=True)
            shutil.rmtree(folder)
            if isinstance(exc,ValueError):raise ValueError(redact(str(exc),token)) from None
            raise ValueError('Worker export failed; inspect owned status and retry.') from None
    return {'message':'Saved validated worker evidence privately; Explorer can import this bundle without compute.','reference':REFERENCE,'artifacts':[{'description':'Complete investigation bundle','path':str(artifact)}],'bundle_id':name,'run_ids':meta['run_ids'],'lens_ids':meta['lens_ids']}
