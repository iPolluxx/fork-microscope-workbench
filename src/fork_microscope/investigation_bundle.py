"""Portable JSON investigations: data only, validated before installation."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from fork_microscope.evidence_io import RUN_ID, MAX_IMPORT_BYTES, validate_export, import_export

SCHEMA = 'fork-investigation-bundle-v1'
PATCH_SCHEMA = 'fork-investigation-bundle-v2'
# Only operational metadata is removed. Prompt/continuation text is evidence and
# must never be silently rewritten. Authors should review it before sharing.
PRIVATE_KEYS = {'token','access_token','api_key','authorization','password','worker_url',
                'hub_cache','cache_dir','checkpoint_path','local_path','path','weights_path','worker_job','error'}

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

def build_bundle(runs, lenses=(), investigation=None, patches=()):
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
    return build_bundle(runs,lenses,investigation,patches)

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
        for lens in value['payload']['lenses'] + value['payload'].get('patches',[]):
            dest=root/'investigations'/(lens['id']+'.json')
            if dest.is_symlink():raise ValueError('Refusing a symlinked inspection destination.')
            if dest.exists():
                if portable(json.loads(dest.read_text()))!=lens:raise ValueError('Conflicting inspection artifact ID; nothing replaced.')
            else:
                src=stage/(lens['id']+'.json');src.write_bytes(canonical(lens));planned.append((src,dest))
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
