# generated: Codex — versioned portable investigation evidence, v1/v2 compatible.
"""Portable JSON investigations: data only, validated before installation."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile

from fork_microscope.evidence_io import RUN_ID, MAX_IMPORT_BYTES, validate_export, import_export

SCHEMA = 'fork-investigation-bundle-v1'
PATCH_SCHEMA = 'fork-investigation-bundle-v2'
V3_SCHEMA = 'fork-investigation-bundle-v3'
# Only operational metadata is removed. Prompt/continuation text is evidence and
# must never be silently rewritten. Authors should review it before sharing.
PRIVATE_KEYS = {'token','access_token','api_key','authorization','password','worker_url',
                'machine_id','compute_binding','hub_cache','cache_dir','checkpoint_path','local_path','path','weights_path','worker_job','error'}

def portable(value):
    if isinstance(value, list): return [portable(x) for x in value]
    if isinstance(value, dict):
        out = {}
        for k,v in value.items():
            if k.lower() in PRIVATE_KEYS or k in ('loading','generation_defaults','import-info'): continue
            if k == 'source_type' and v == 'local': out[k] = v; continue
            if k == 'model_id' and isinstance(v,str) and (v.startswith(('/', '~', '\\')) or ':\\' in v):
                out[k] = 'local-model'; continue
            out[k] = portable(v)
        return out
    return value

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

def normalized_numbers(value):
    """JSON browsers serialize 1.0 as 1 and -0.0 as 0; hash their equal values.

    Keep nonintegral floats unchanged and never coerce integers to floats: a
    browser losing precision on a large integer must still fail verification.
    """
    if isinstance(value, dict): return {k: normalized_numbers(v) for k,v in value.items()}
    if isinstance(value, list): return [normalized_numbers(v) for v in value]
    if isinstance(value, float) and value.is_integer(): return int(value)
    return value

def digest(value): return hashlib.sha256(canonical(normalized_numbers(value))).hexdigest()

def legacy_digest(value): return hashlib.sha256(canonical(value)).hexdigest()

def identifier(value):
    if not isinstance(value,str) or not RUN_ID.fullmatch(value): raise ValueError('Invalid artifact ID.')
    return value

def build_bundle(runs, lenses=(), investigation=None, patches=(), responses=(), edits=(), captures=()):
    if (investigation or {}).get("schema") == "fork-workflow-v2" or responses or edits or captures:
        return build_bundle_v3(runs, lenses, investigation, patches, responses, edits, captures)
    job = {k:v for k,v in (investigation or {}).items() if k in {'id','schema','status','config','created','runs','lenses','steps','reservations','elapsed_seconds'}}
    payload = portable(dict(runs=list(runs), lenses=list(lenses), investigation=job))
    patches = list(patches)
    if patches:
        payload['patches'] = portable(patches)
    ids = [r['id'] for r in payload['runs']]
    manifest = dict(run_ids=ids, lens_ids=[l['id'] for l in payload['lenses']],
                    entry_run_id=ids[-1] if ids else None,
                    note='Stored fits are not recomputed. Review prompt and response text for private content before sharing.')
    if patches:
        manifest['patch_ids'] = [p['id'] for p in payload['patches']]
    out = dict(schema=PATCH_SCHEMA if patches else SCHEMA, manifest=manifest, payload=payload, sha256=digest(payload))
    validate_bundle(out)
    return out

def validate_lens(lens, runs):
    if not isinstance(lens,dict) or lens.get('schema') != 'fork-lens-v1' or lens.get('status') != 'complete':
        raise ValueError('Only completed version-1 lens readouts can be bundled.')
    identifier(lens.get('id'))
    req=lens.get('request',{});run=runs.get(req.get('source_run_id'))
    if not run or req.get('source_pass_id') not in run['records']:raise ValueError('Lens source run/pass is missing.')
    if any(lens.get('model',{}).get(k) != run['model'].get(k) for k in ('model_id','resolved_revision')):raise ValueError('Lens source model mismatch.')
    if not isinstance(lens.get('cells'),list) or not isinstance(lens.get('arms'),list):raise ValueError('Malformed lens readout.')
    arms={a.get('id'):a for a in lens['arms'] if isinstance(a,dict)}
    if len(arms)!=len(lens['arms']) or not arms:raise ValueError('Invalid lens arms.')
    record=run['records'][req['source_pass_id']];base=record['base']
    for arm in arms.values():
        if arm.get('prompt_ids')!=base['prompt_ids']:raise ValueError('Lens prompt differs from its source.')
        draw=arm.get('source_draw')
        if draw:
            matches=[base['gen_ids'][:b['t']]+[b['tok_id']]+b['continuation_ids'][i]
                     for b in record['branches'] if b['t']==draw.get('checkpoint')
                     for i,d in enumerate(b.get('draw_indices',[])) if d==draw.get('draw_index')]
            if matches!=[arm.get('response_ids')]:raise ValueError('Lens continuation differs from its source draw.')
        if arm.get('input_ids') != (arm.get('prompt_ids',[])+arm.get('response_ids',[]))[:len(arm.get('input_ids',[]))]:raise ValueError('Lens replay input is inconsistent.')
        if not isinstance(arm.get('input_ids'),list) or any(type(t) is not int or t<0 for t in arm['input_ids']):raise ValueError('Invalid lens token IDs.')
        if hashlib.sha256(json.dumps(arm['input_ids'], sort_keys=True).encode()).hexdigest() != arm.get('prefix_sha256'):raise ValueError('Lens prefix checksum mismatch.')
    for c in lens['cells']:
        if not isinstance(c,dict) or c.get('arm') not in arms:raise ValueError('Lens cell has an unknown arm.')
        if any(type(c.get(k)) is not int or c[k]<0 for k in ('layer','index','absolute_position','token_id')):raise ValueError('Invalid lens cell coordinates.')
        if c['layer'] not in req.get('layers',[]) or not req.get('start',0)<=c['index']<=req.get('end',-1):raise ValueError('Lens cell falls outside the requested region.')
        for name in ('tokens','logit_lens_tokens','model_tokens'):
            if not isinstance(c.get(name),list):raise ValueError('Missing vocabulary readout.')

def validate_patch(patch, runs):
    """Validate data-only patch artifacts against their bundled exact source IDs.

    This checks provenance and internal consistency, not the scientific truth of
    a submitted experiment. Imports never execute hooks or load model weights.
    """
    if not isinstance(patch,dict) or patch.get('schema')!='fork-activation-patch-v1' or patch.get('status')!='complete':
        raise ValueError('Only completed version-1 patch experiments can be bundled.')
    identifier(patch.get('id'))
    q=patch.get('request',{})
    fields={'source_run_id','source_pass_id','donor','recipient','layers','samples','cont_max','temperature','seed','selection_rationale','max_seconds'}
    if not isinstance(q,dict) or set(q)!=fields:raise ValueError('Invalid patch request fields.')
    run=runs.get(q['source_run_id'])
    if not run or q['source_pass_id'] not in run['records']:raise ValueError('Patch source run/pass is missing.')
    if any(patch.get('model',{}).get(k)!=run['model'].get(k) for k in ('model_id','resolved_revision')):raise ValueError('Patch source model mismatch.')
    def whole(v,low,high):return type(v) is int and low<=v<=high
    for key,lo,hi in [('samples',2,32),('cont_max',1,512),('seed',0,2**31-1),('max_seconds',1,1800)]:
        if not whole(q[key],lo,hi):raise ValueError('Invalid patch resource settings.')
    if type(q['temperature']) not in (int,float) or not .05<=q['temperature']<=2:raise ValueError('Invalid patch temperature.')
    if not isinstance(q['selection_rationale'],str) or not 1<=len(q['selection_rationale'].strip())<=2000:raise ValueError('Missing patch selection rationale.')
    if not isinstance(q['layers'],list) or not 1<=len(q['layers'])<=4 or any(not whole(x,0,10000) for x in q['layers']) or len(set(q['layers']))!=len(q['layers']):raise ValueError('Invalid patch layers.')
    record=run['records'][q['source_pass_id']];base=record['base']
    for key in ('prompt_ids','gen_ids','base_text'):
        if patch.get('source_base',{}).get(key)!=base[key]:raise ValueError('Patch source trace differs from its run.')
    source_ids={'prompt_ids':base['prompt_ids'],'gen_ids':base['gen_ids']}
    exact_hash=lambda x:hashlib.sha256(json.dumps(x,sort_keys=True).encode()).hexdigest()
    if patch.get('source_ids_sha256')!=exact_hash(source_ids):raise ValueError('Patch source checksum mismatch.')
    prefixes=patch.get('prefixes')
    if not isinstance(prefixes,dict) or set(prefixes)!={'donor','recipient'}:raise ValueError('Missing patch prefixes.')
    for name,prefix in prefixes.items():
        selection=q[name]
        if not isinstance(selection,dict) or set(selection)!={'selection','position'}:raise ValueError('Invalid patch trajectory selection.')
        choice=selection['selection'];position=selection['position']
        if not isinstance(choice,dict):raise ValueError('Invalid patch source selection.')
        if choice=={'type':'original'}:
            response=base['gen_ids']
        elif set(choice)=={'type','checkpoint','draw_index'} and choice['type']=='draw' and whole(choice['checkpoint'],0,len(base['gen_ids'])-1) and whole(choice['draw_index'],0,10**9):
            matches=[base['gen_ids'][:b['t']]+[b['tok_id']]+b['continuation_ids'][i]
                     for b in record['branches'] if b['t']==choice['checkpoint']
                     for i,d in enumerate(b.get('draw_indices',[])) if d==choice['draw_index']]
            if len(matches)!=1:raise ValueError('Patch source continuation is missing or ambiguous.')
            response=matches[0]
        else:raise ValueError('Unsupported patch source selection.')
        if not whole(position,0,len(response)-1):raise ValueError('Patch position falls outside its source.')
        ids=base['prompt_ids']+response[:position+1]
        expected=dict(selection=choice,position=position,absolute_position=len(ids)-1,prompt_ids=base['prompt_ids'],response_ids=response[:position+1],input_ids=ids,prefix_sha256=exact_hash(ids),token_id=ids[-1])
        if not isinstance(prefix,dict) or any(prefix.get(k)!=v for k,v in expected.items()):raise ValueError('Patch prefix differs from its saved source.')
        if any(not isinstance(prefix.get(k),str) for k in ('token_text','prefix_text')):raise ValueError('Missing patch text previews.')
    if prefixes['donor']['input_ids']==prefixes['recipient']['input_ids']:raise ValueError('Identical patch prefixes cannot establish a contrast.')
    if patch.get('continuations')!=3*q['samples'] or patch.get('max_new_tokens')!=3*q['samples']*q['cont_max']:raise ValueError('Patch budget metadata mismatch.')
    if patch.get('answers')!=run.get('base_config',{}).get('answers'):raise ValueError('Patch outcome rules differ from the source.')
    observations=patch.get('observations')
    if not isinstance(observations,list) or len(observations)!=3*q['samples']:raise ValueError('Missing patch control/intervention draws.')
    arms=('baseline','identity','patched');seen={};counts={a:{} for a in arms}
    vocab=run['model'].get('vocab_size',2**31)
    labels=run.get('base_config',{}).get('answers') or ['A','B','C','D']
    for o in observations:
        if not isinstance(o,dict) or o.get('arm') not in arms or not whole(o.get('draw'),0,q['samples']-1):raise ValueError('Invalid patch observation.')
        key=(o['arm'],o['draw'])
        if key in seen:raise ValueError('Duplicate patch observation.')
        seen[key]=o
        if o.get('seed')!=(q['seed']+o['draw'])%(2**31-1):raise ValueError('Patch draw seed mismatch.')
        ids=o.get('continuation_ids')
        if not isinstance(ids,list) or len(ids)>q['cont_max'] or any(not whole(t,0,vocab-1) for t in ids):raise ValueError('Invalid patch continuation tokens.')
        if o.get('label') not in labels+['Other'] or o.get('stop_reason') not in ('eos','length'):raise ValueError('Invalid patch classification.')
        if not isinstance(o.get('continuation_text'),str) or not isinstance(o.get('full_response_text'),str):raise ValueError('Missing patch continuation text.')
        if o.get('prefill_patch_count')!=(0 if o['arm']=='baseline' else len(q['layers'])):raise ValueError('Invalid patch intervention count.')
        counts[o['arm']][o['label']]=counts[o['arm']].get(o['label'],0)+1
    summary=patch.get('summary',{})
    matches=sum(seen[('baseline',i)]['continuation_ids']==seen[('identity',i)]['continuation_ids'] for i in range(q['samples']))
    if summary.get('counts')!=counts or summary.get('identity_control')!=dict(draws_compared=q['samples'],exact_matches=matches,passed=matches==q['samples']) or summary.get('capped')!=sum(o['stop_reason']=='length' for o in observations):raise ValueError('Patch summary differs from its observations.')

def validate_bundle(value):
    if isinstance(value, dict) and value.get("schema") == V3_SCHEMA:
        return validate_bundle_v3(value)
    try:
        if set(value)!={'schema','manifest','payload','sha256'} or value['schema'] not in (SCHEMA,PATCH_SCHEMA):raise ValueError('Unsupported investigation bundle.')
        if len(canonical(value))>MAX_IMPORT_BYTES:raise ValueError('Investigation bundle exceeds 64 MB.')
        p=value['payload'];m=value['manifest']
        keys = {'runs','lenses','investigation'} | ({'patches'} if value['schema']==PATCH_SCHEMA else set())
        if set(p)!=keys or not isinstance(p['investigation'],dict):raise ValueError('Malformed bundle payload.')
        # Accept untouched early v1 files as well as browser-stable new exports.
        if digest(p)!=value['sha256'] and legacy_digest(p)!=value['sha256']:raise ValueError('Investigation checksum mismatch.')
        if not isinstance(p['runs'],list) or not 1<=len(p['runs'])<=100 or not isinstance(p['lenses'],list) or len(p['lenses'])>100:raise ValueError('Invalid bundle artifact counts.')
        if portable(p)!=p:raise ValueError('Bundle includes private operational metadata; export it through the bundle exporter.')
        runs={identifier(r['id']):validate_export(r) for r in p['runs']}
        if len(runs)!=len(p['runs']) or m['run_ids']!=list(runs) or m['entry_run_id'] not in runs:raise ValueError('Invalid run manifest.')
        for r in runs.values():
            parent=(r.get('lineage') or {}).get('source_run_id')
            if parent and parent not in runs:raise ValueError('A parent run is missing from this bundle.')
            seen={r['id']};cursor=parent
            while cursor:
                if cursor in seen:raise ValueError('Run lineage contains a cycle.')
                seen.add(cursor);cursor=(runs[cursor].get('lineage') or {}).get('source_run_id')
            if parent:
                a=next(iter(r['records'][x['id']]['base'] for x in r['passes']))
                b=next(iter(runs[parent]['records'][x['id']]['base'] for x in runs[parent]['passes']))
                if (a['prompt_ids'],a['gen_ids'])!=(b['prompt_ids'],b['gen_ids']):raise ValueError('Refinement does not preserve parent token IDs.')
        ids=[]
        for l in p['lenses']:validate_lens(l,runs);ids.append(l['id'])
        if len(set(ids))!=len(ids) or m['lens_ids']!=ids:raise ValueError('Invalid lens manifest.')
        if set(ids) & set(runs):raise ValueError('Run and lens IDs must be distinct.')
        if value['schema']==PATCH_SCHEMA:
            if not isinstance(p['patches'],list) or not 1<=len(p['patches'])<=100:raise ValueError('Invalid patch artifact count.')
            patch_ids=[]
            for patch in p['patches']:
                validate_patch(patch,runs)
                patch_ids.append(patch['id'])
            if len(set(patch_ids))!=len(patch_ids) or m.get('patch_ids')!=patch_ids:raise ValueError('Invalid patch manifest.')
            if set(patch_ids) & (set(ids)|set(runs)):raise ValueError('Artifact IDs must be distinct across kinds.')
    except (KeyError,TypeError,IndexError,AttributeError,RecursionError) as exc:
        raise ValueError('Malformed investigation bundle.') from exc
    return value

def export_family(service, run_id):
    """Include the root, all locally available descendants, and their readouts."""
    catalog={r['id']:r for r in service.results()}
    root=identifier(run_id);seen=set()
    while root not in seen:
        seen.add(root);r=service.result(root,raw=True);parent=(r.get('lineage') or {}).get('source_run_id')
        if not parent:break
        root=parent
    else:raise ValueError('Cyclic lineage.')
    selected={root};changed=True
    while changed:
        changed=False
        for r in catalog.values():
            if r['id'] not in selected and (r.get('lineage') or {}).get('source_run_id') in selected:
                selected.add(r['id']);changed=True
    runs=sorted((service.result(i,raw=True) for i in selected),key=lambda r:r['created'])
    lenses=[service.investigation(l['id']) for l in service.investigations()
            if l.get('schema')=='fork-lens-v1' and l.get('status')=='complete' and (l.get('request') or {}).get('source_run_id') in selected]
    patches=[service.investigation(l['id']) for l in service.investigations()
             if l.get('schema')=='fork-activation-patch-v1' and l.get('status')=='complete' and (l.get('request') or {}).get('source_run_id') in selected]
    investigation={}
    manager=getattr(service,'workflow_manager',None)
    if manager:
        for job in manager.list():
            if root in job.get('runs',[]):investigation=job;break
    if not investigation:
        # Preserve the handoff manifest when re-exporting an imported family.
        from fork_microscope import live_service
        for path in (live_service.RUNS.parent/'investigation-bundles').glob('*.json'):
            archived=json.loads(path.read_text())
            if root in archived.get('manifest',{}).get('run_ids',[]):
                investigation=archived['payload']['investigation'];break
    artifacts=[service.investigation(l['id']) for l in service.investigations()
        if l.get('schema')=='fork-investigation-v1' and (l.get('request') or {}).get('source_run_id') in selected]
    edits=[x for x in artifacts if x['request']['kind']=='edit'];captures=[x for x in artifacts if x['request']['kind']=='activation']
    response_ids=list(dict.fromkeys(list(investigation.get('responses',[]))+[r['source_response_id'] for r in runs if r.get('source_response_id')]))
    responses=[service.response(i) for i in response_ids]
    if investigation.get('schema')=='fork-workflow-v2' and set(investigation['runs']) != selected:
        return manager.export(investigation['id']) if manager else build_bundle(runs,lenses,investigation,patches,responses,edits,captures)
    return build_bundle(runs,lenses,investigation,patches,responses,edits,captures)

def import_bundle(value, runs):
    validate_bundle(value);runs=Path(runs);root=runs.parent
    root.mkdir(parents=True,exist_ok=True)
    # Stage and validate everything before touching installed evidence. Journaled
    # moves make a crash recoverable by retry; existing artifacts are never replaced.
    with tempfile.TemporaryDirectory(prefix='.bundle-',dir=root) as directory:
        stage=Path(directory);planned=[]
        for r in value['payload']['runs']:
            dest=runs/r['id']
            if dest.is_symlink():raise ValueError('Refusing a symlinked run destination.')
            if dest.exists():
                existing=json.loads((dest/'result.json').read_text())
                existing['records']={p.stem:json.loads(p.read_text()) for p in dest.glob('*.json') if p.stem not in ('result','manifest','import-info')}
                if portable(existing)!=r:raise ValueError('Conflicting existing run ID; nothing replaced.')
            else:
                import_export(r,stage/'live-runs');planned.append((stage/'live-runs'/r['id'],dest))
        for lens in value['payload']['lenses'] + value['payload'].get('patches',[]) + value['payload'].get('edits',[]) + value['payload'].get('captures',[]):
            dest=root/'investigations'/(lens['id']+'.json')
            if dest.is_symlink():raise ValueError('Refusing a symlinked inspection destination.')
            if dest.exists():
                if portable(json.loads(dest.read_text()))!=lens:raise ValueError('Conflicting inspection artifact ID; nothing replaced.')
            else:
                src=stage/(lens['id']+'.json');src.write_bytes(canonical(lens));planned.append((src,dest))
        for response in value['payload'].get('responses', []):
            dest=root/'responses'/(response['id']+'.json')
            if dest.is_symlink(): raise ValueError('Refusing a symlinked response destination.')
            if dest.exists():
                if portable(json.loads(dest.read_text())) != response: raise ValueError('Conflicting response ID; nothing replaced.')
            else:
                src=stage/('response-'+response['id']+'.json');src.write_bytes(canonical(response));planned.append((src,dest))
        if value['schema'] == V3_SCHEMA:
            record=value['payload']['investigation']
            dest=root/'workflow-jobs'/(record['id']+'.json')
            if dest.is_symlink(): raise ValueError('Refusing a symlinked investigation destination.')
            if dest.exists():
                if portable(json.loads(dest.read_text())) != record: raise ValueError('Conflicting investigation ID; nothing replaced.')
            else:
                src=stage/'workflow.json';src.write_bytes(canonical(record));planned.append((src,dest))
        archive=root/'investigation-bundles'/(value['sha256']+'.json')
        src=stage/'bundle.json';src.write_bytes(canonical(value))
        if not archive.exists():planned.append((src,archive))
        for src,dest in planned:
            dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists():raise ValueError('Evidence changed during import; retry without overwriting.')
            src.rename(dest)
    return dict(id=value['manifest']['entry_run_id'],run_ids=value['manifest']['run_ids'],
                lens_ids=value['manifest']['lens_ids'],patch_ids=value['manifest'].get('patch_ids',[]),
                already_present=not planned,bundle_sha256=value['sha256'])


def build_bundle_v3(runs, lenses=(), investigation=None, patches=(), responses=(), edits=(), captures=()):
    from fork_microscope.investigation_records import adapt_workflow
    collections = dict(runs=list(runs), lenses=list(lenses), patches=list(patches), responses=list(responses), edits=list(edits), captures=list(captures))
    job = copy.deepcopy(investigation or {})
    if not job:
        root_ids = sorted(x['id'] for x in collections['runs'] if not (x.get('lineage') or {}).get('source_run_id'))
        job = dict(id=digest({'family':root_ids})[:32], schema='fork-workflow-v2', status='idle', config=None, created=None,
                   runs=[x['id'] for x in collections['runs']], lenses=[x['id'] for x in collections['lenses']], steps=[],
                   reservations={'samples':None,'generated_tokens':None}, elapsed_seconds=None)
    job = adapt_workflow(job);job['schema']='fork-workflow-v2'
    for kind in collections:
        present = [x['id'] for x in collections[kind]]
        missing = set(job.get(kind,[])) - set(present)
        if missing:raise ValueError('Cannot export: missing '+kind+' '+', '.join(sorted(missing)))
        job[kind] = present
    # Live process details and operation requests are not execution authority on import.
    job.pop('worker_job',None)
    if job.get('pending') or job.get('status') == 'running':
        raise ValueError('Cannot export an unfinished operation; completed evidence remains saved locally.')
    payload = portable(dict(collections, investigation=job))
    manifest = {{'lenses':'lens_ids','patches':'patch_ids'}.get(kind,kind[:-1]+'_ids'):[x['id'] for x in payload[kind]] for kind in collections}
    manifest.update(entry_run_id=job['runs'][-1] if job['runs'] else None, entry_response_id=job.get('selected_response_id'), minimum_reader_version=3,
                    note='Checksums verify file consistency, not scientific truth. Stored fits are not recomputed.')
    value = dict(schema=V3_SCHEMA, manifest=manifest, payload=payload, sha256=digest(payload))
    validate_bundle_v3(value)
    return value


def validate_basic_artifact(value, runs, kind):
    from fork_microscope.investigation_records import exact_hash
    if not isinstance(value,dict) or value.get('schema') != 'fork-investigation-v1' or value.get('status') != 'complete':
        raise ValueError('Only complete edit and capture artifacts can be exported; unfinished items remain local.')
    identifier(value.get('id'));q=value.get('request',{})
    if q.get('kind') != kind:raise ValueError('Artifact kind mismatch.')
    fields={'source_run_id','source_pass_id','kind'} | ({'positions','layers'} if kind=='activation' else {'start','end','replacement','samples','cont_max','temperature','seed'})
    if 'source_selection' in q: fields.add('source_selection')
    if set(q)!=fields:raise ValueError('Invalid edit/capture request fields.')
    run=runs.get(q.get('source_run_id'));record=(run or {}).get('records',{}).get(q.get('source_pass_id'))
    if record is None:raise ValueError('Edit/capture source run or pass is missing.')
    base=record['base']
    if q.get('source_selection'):
        choice=q['source_selection']
        if choice.get('schema') != 'fork-trajectory-v1' or choice.get('type') != 'draw' or set(choice)!={'schema','type','checkpoint','draw_index'}:raise ValueError('Invalid trajectory selector.')
        matches=[base['gen_ids'][:b['t']]+[b['tok_id']]+b['continuation_ids'][i] for b in record['branches'] if b['t']==choice['checkpoint'] for i,d in enumerate(b.get('draw_indices',[])) if d==choice['draw_index']]
        if len(matches)!=1:raise ValueError('Selected source continuation is missing.')
        base=dict(base,gen_ids=matches[0])
    if any(value.get('model',{}).get(k)!=run['model'].get(k) for k in ('model_id','resolved_revision')):raise ValueError('Edit/capture model mismatch.')
    if any(value.get('source_base',{}).get(k)!=base[k] for k in ('prompt_ids','gen_ids')):raise ValueError('Edit/capture token source mismatch.')
    if value.get('source_ids_sha256')!=exact_hash({k:base[k] for k in ('prompt_ids','gen_ids')}):raise ValueError('Edit/capture source checksum mismatch.')
    vocab=run['model'].get('vocab_size',2**31)
    def tokens(ids):return isinstance(ids,list) and all(type(x) is int and 0<=x<vocab for x in ids)
    if kind=='activation':
        positions=q.get('positions',[]);layers=q.get('layers',[])
        if not positions or not layers or len(positions)>16 or len(layers)>4 or len(set(positions))!=len(positions) or len(set(layers))!=len(layers):raise ValueError('Invalid capture selection.')
        if any(type(x) is not int or not 0<=x<=len(base['gen_ids']) for x in positions) or any(type(x) is not int or x<0 for x in layers):raise ValueError('Invalid capture coordinates.')
        rows=value.get('captures');expected={(p,l) for p in positions for l in layers}
        if not isinstance(rows,list) or len(rows)!=len(expected):raise ValueError('Incomplete capture rows.')
        seen=set()
        for row in rows:
            key=(row['checkpoint'],row['layer']);prefix=base['prompt_ids']+base['gen_ids'][:row['checkpoint']]
            if key not in expected or key in seen:raise ValueError('Duplicate or out-of-scope capture row.')
            seen.add(key)
            if row.get('prefix_ids')!=prefix or row.get('prefix_sha256')!=exact_hash(prefix) or row.get('absolute_position')!=len(prefix)-1:raise ValueError('Capture prefix provenance mismatch.')
            vector=row.get('vector')
            if not isinstance(vector,list) or not vector or any(type(x) not in (int,float) for x in vector):raise ValueError('Invalid capture vector.')
            if type(row.get('norm')) not in (int,float) or not math.isclose(row['norm'],math.sqrt(sum(x*x for x in vector)),rel_tol=1e-4,abs_tol=1e-6):raise ValueError('Capture norm differs from its vector.')
    else:
        start,end=q.get('start'),q.get('end')
        if type(start) is not int or type(end) is not int or not 0<=start<end<=len(base['gen_ids']):raise ValueError('Invalid edit span.')
        replacement=value.get('replacement_ids')
        if not tokens(replacement):raise ValueError('Invalid replacement token IDs.')
        arms={'control':base['gen_ids'][:end],'edit':base['gen_ids'][:start]+replacement}
        if value.get('arms')!=arms:raise ValueError('Edit prefix provenance mismatch.')
        samples,cap=q.get('samples'),q.get('cont_max')
        if type(samples) is not int or not 2<=samples<=128 or type(cap) is not int or not 1<=cap<=4096:raise ValueError('Invalid edit bounds.')
        if type(q.get('seed')) is not int or not 0<=q['seed']<=2**31-1 or type(q.get('temperature')) not in (int,float) or not .05<=q['temperature']<=2:raise ValueError('Invalid edit sampling settings.')
        if not isinstance(q.get('replacement'),str) or len(q['replacement'])>16000:raise ValueError('Invalid edit replacement text.')
        observations=value.get('observations')
        if not isinstance(observations,list) or len(observations)!=samples*2:raise ValueError('Incomplete edit observations.')
        seen=set()
        for row in observations:
            key=(row.get('arm'),row.get('draw'))
            if key[0] not in arms or type(key[1]) is not int or not 0<=key[1]<samples or key in seen:raise ValueError('Invalid edit observation coordinates.')
            seen.add(key)
            if not tokens(row.get('continuation_ids')) or len(row['continuation_ids'])>cap:raise ValueError('Invalid edit continuation tokens.')
            expected_seed=(q['seed']+2*key[1]+(1 if key[0]=='edit' else 0))%(2**31-1)
            if row.get('seed')!=expected_seed or row.get('stop_reason') not in ('eos','length'):raise ValueError('Invalid edit seed or finish status.')
            if row.get('label') not in (run.get('base_config',{}).get('answers') or ['A','B','C','D'])+['Other']:raise ValueError('Unknown edit outcome.')


def validate_bundle_v3(value):
    try:
        if set(value)!={'schema','manifest','payload','sha256'}:raise ValueError('Malformed v3 bundle envelope.')
        if len(canonical(value))>MAX_IMPORT_BYTES:raise ValueError('Complete investigation export exceeds 64 MiB; no evidence was omitted.')
        p=value['payload'];m=value['manifest'];kinds=('runs','lenses','patches','responses','edits','captures')
        if set(p)!=set(kinds)|{'investigation'} or digest(p)!=value['sha256']:raise ValueError('Invalid v3 payload or checksum.')
        if portable(p)!=p:raise ValueError('Bundle contains private operational metadata.')
        if any(not isinstance(p[k],list) or len(p[k])>100 for k in kinds):raise ValueError('Invalid v3 artifact counts.')
        all_ids=[]
        for k in kinds:
            ids=[identifier(x['id']) for x in p[k]];all_ids.extend(ids)
            key={'lenses':'lens_ids','patches':'patch_ids'}.get(k,k[:-1]+'_ids')
            if m.get(key)!=ids:raise ValueError('Artifact manifest mismatch for '+k)
        if len(set(all_ids))!=len(all_ids):raise ValueError('Duplicate artifact IDs across kinds.')
        runs={r['id']:r for r in p['runs']}
        if runs:
            # Apply the unchanged v1/v2 run, lineage, lens, and patch validators.
            old=build_bundle(p['runs'],p['lenses'],patches=p['patches'])
            if m.get('entry_run_id') not in runs:raise ValueError('Missing entry scan.')
        elif p['lenses'] or p['patches'] or p['edits'] or p['captures'] or m.get('entry_run_id') is not None:
            raise ValueError('Inspections require their source scans.')
        from fork_microscope.investigation_records import validate_response, validate_context
        responses={r['id']:validate_response(r) for r in p['responses']}
        for kind,name in [('edit','edits'),('activation','captures')]:
            for artifact in p[name]:validate_basic_artifact(artifact,runs,kind)
        job=p['investigation']
        if not isinstance(job,dict) or job.get('schema')!='fork-workflow-v2':raise ValueError('v3 requires a workflow v2 record.')
        identifier(job.get('id'))
        if job.get('context',{}).get('provenance')!='legacy_inferred':validate_context(job.get('context'))
        for k in kinds:
            if job.get(k)!=[x['id'] for x in p[k]]:raise ValueError('Investigation index differs from bundled '+k)
        if job.get('selected_response_id') is not None and job['selected_response_id'] not in responses:raise ValueError('Missing selected response.')
        if m.get('entry_response_id')!=job.get('selected_response_id'):raise ValueError('Entry response mismatch.')
        for search in job.get('searches',[]):
            if any(i not in responses for i in search['response_ids']):raise ValueError('Search response is missing.')
        from fork_microscope.investigation_catalog import InvestigationCatalog
        for comparison in job.get('comparisons',[]):InvestigationCatalog.validate_comparison(comparison,runs)
        edits={x['id']:x for x in p['edits']}
        for lens in p['lenses']:
            q=lens['request'];selection=q.get('selection',{})
            base=runs[q['source_run_id']]['records'][q['source_pass_id']]['base']
            for arm in lens['arms']:
                if selection.get('type')=='original' and arm.get('response_ids')!=base['gen_ids']:raise ValueError('Lens original response differs from its source.')
                if selection.get('type')=='edit_pair':
                    edit=edits.get(selection.get('investigation_id'))
                    if not edit or edit['request']['source_run_id']!=q['source_run_id'] or edit['request']['source_pass_id']!=q['source_pass_id']:raise ValueError('Lens source edit is missing or incompatible.')
                    if arm.get('response_ids')!=edit['arms'].get(arm['id']):raise ValueError('Lens edited prefix differs from its source.')
                if selection.get('type')=='draw_pair':
                    draw=arm.get('source_draw',{})
                    if draw.get('checkpoint')!=selection.get('checkpoint') or draw.get('draw_index') not in selection.get('draw_indices',[]):raise ValueError('Lens draw differs from its requested selection.')
                expected=(arm['prompt_ids']+arm['response_ids'])[:(len(arm['prompt_ids']) if q['space']=='response' else 0)+q['end']+1]
                if arm['input_ids']!=expected:raise ValueError('Lens replay prefix does not match its requested endpoint.')
        for run in runs.values():
            parent=(run.get('lineage') or {}).get('source_run_id')
            if parent and any(run['model'].get(k)!=runs[parent]['model'].get(k) for k in ('model_id','resolved_revision')):raise ValueError('Refinement model differs from its parent.')
            rid=run.get('source_response_id')
            if rid:
                if rid not in responses:raise ValueError('Source response is missing.')
                response=responses[rid]
                if any(response['model'].get(k)!=run['model'].get(k) for k in ('model_id','resolved_revision')):raise ValueError('Response/run model mismatch.')
                for pass_entry in run['passes']:
                    record=run['records'][pass_entry['id']]
                    if any(record['base'][k]!=response['base'][k] for k in ('prompt_ids','gen_ids')):raise ValueError('Scan differs from exact source response tokens.')
    except (KeyError,TypeError,IndexError,AttributeError,RecursionError) as exc:
        raise ValueError('Malformed v3 investigation bundle.') from exc
    return value
