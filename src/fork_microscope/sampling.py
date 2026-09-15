"""Versioned pass plans and direct position-level mixture observations."""
import math
import numpy as np

COMMON = {'cont_max','temperature','top_k','threshold','dense','reference_samples','tuning'}

def integer(v, name, lo, hi):
    if type(v) is not int or not lo <= v <= hi:
        raise ValueError(f'{name} must be a whole number from {lo} to {hi}.')

def pass_plan(c, last):
    """Accept legacy paired configs; new configs require explicit passes."""
    if type(c) is not dict:
        raise ValueError('Expected a run configuration.')
    legacy = 'passes' not in c
    expected = COMMON | ({'samples','stride','shift','start','end','seed'} if legacy else {'passes'})
    if set(c) != expected: raise ValueError('Unexpected or missing fields.')
    for key,lo,hi in [('cont_max',1,4096),('top_k',1,50),('reference_samples',5,512)]:
        integer(c[key],key,lo,hi)
    for key,lo,hi in [('temperature',.05,2),('threshold',0,1)]:
        if type(c[key]) not in (float,int) or not math.isfinite(c[key]) or not lo<=c[key]<=hi:
            raise ValueError(f'Invalid {key}.')
    if type(c['dense']) is not bool or c['tuning'] not in ('cv','fixed'):
        raise ValueError('Choose dense reference on/off and CV or fixed tuning.')
    if legacy:
        integer(c['stride'],'stride',2,128)
        integer(c['shift'],'shift',1,c['stride']-1)
        integer(c['start'],'start',0,last);integer(c['end'],'end',0,last)
        first=list(range(c['start'],c['end']-c['shift']+1,c['stride']))
        if len(first)<2: raise ValueError('Choose at least two paired checkpoints.')
        specs=[dict(id=k,label=f'Pass {i+1}',start=c['start'],end=first[-1]+i*c['shift'],
            stride=c['stride'],offset=i*c['shift'],samples=c['samples'],seed=c['seed'])
            for i,k in enumerate(('first','second'))]
    else:
        specs=c['passes']
        if type(specs) is not list or not 1<=len(specs)<=8:
            raise ValueError('Configure between one and eight passes.')
    plan=[];seen=set()
    for p in specs:
        if type(p) is not dict or set(p)-{'positions'}!={'id','label','start','end','stride','offset','samples','seed'}:
            raise ValueError('A pass requires id, label, start, end, stride, offset, samples and seed.')
        import re
        if not isinstance(p['id'],str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}',p['id']) or p['id']=='dense' or p['id'] in seen:
            raise ValueError('Pass IDs must be unique safe names, other than dense.')
        if not isinstance(p['label'],str) or not p['label'].strip() or len(p['label'])>80:
            raise ValueError('Enter a pass name up to 80 characters.')
        seen.add(p['id'])
        for key,lo,hi in [('start',0,last),('end',0,last),('stride',1,128),('offset',0,p['stride']-1 if type(p['stride']) is int else 0),('samples',5,512),('seed',0,2**31-1)]:
            integer(p[key],key,lo,hi)
        positions=list(range(p['start']+p['offset'],p['end']+1,p['stride']))
        if 'positions' in p:
            positions=p['positions']
            if type(positions) is not list or not 2<=len(positions)<=4096:
                raise ValueError('Explicit positions require 2–4096 checkpoint IDs.')
            for t in positions: integer(t,'checkpoint',p['start'],p['end'])
            if positions!=sorted(set(positions)) or positions[0]!=p['start'] or positions[-1]!=p['end'] or p['offset']!=0:
                raise ValueError('Explicit positions must be ordered, unique, include both endpoints, and have offset zero.')
        if len(positions)<2: raise ValueError(f"{p['label']}: choose at least two checkpoints.")
        plan.append(dict(p,positions=positions))
    return plan


def allocation(branches, samples, seed):
    """Draw branch identities before generation. Never resample observed labels."""
    weights=np.asarray([b.tok_p for b in branches],float)
    if not len(weights) or not np.isfinite(weights).all() or weights.sum()<=0:
        raise ValueError('Checkpoint has no valid branch probabilities.')
    return np.random.default_rng(seed).choice(len(branches),size=samples,p=weights/weights.sum()).tolist()


def position_draws(record):
    """Every generated observation appears once in its original mixture order."""
    rows=record['positions']; categories=record['categories']; grouped={r['t']:{} for r in rows}
    for b in record['branches']:
        if len(b['draw_indices'])!=len(b['answers']): raise ValueError('Draw count mismatch.')
        for i,a in zip(b['draw_indices'],b['answers']):
            if i in grouped[b['t']]: raise ValueError('Duplicate draw index.')
            grouped[b['t']][i]=categories.index(a)
    result=[]
    for r in rows:
        d=grouped[r['t']]
        if set(d)!=set(range(r['samples'])): raise ValueError('Incomplete checkpoint observations.')
        result.append([d[i] for i in range(r['samples'])])
    return [r['t'] for r in rows],np.asarray(result,dtype=np.int8)
