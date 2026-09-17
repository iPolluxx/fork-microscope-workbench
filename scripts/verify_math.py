# generated: Codex — Paper2Agent-guided bounded reconstruction audit, 2026-09-17.
"""CPU-only evidence audit. Run from any cwd with the project .venv Python.
Uses only the pinned upstream release; writes outputs/math-checks.json by default.
"""
from pathlib import Path
import hashlib, importlib.util, itertools, json, os, subprocess, sys, time
os.environ['OTRECON_FORCE_RUPTURES']='1'
ROOT=Path(__file__).resolve().parents[1]
import argparse
parser=argparse.ArgumentParser(description='Verify estimator integration using only public upstream data.')
parser.add_argument('--output',type=Path,default=ROOT/'outputs'/'math-checks.json')
parser.add_argument('--case', action='append', choices=['llama:12','llama:39','deepseek:12','deepseek:39'],
                    help='Audit only the selected public store; repeat for several. Default: all stores and four cases.')
parser.add_argument('--checkpoints', type=int, default=24, choices=range(4,25), metavar='4..24',
                    help='Maximum evenly spaced observed checkpoints per case (default 24).')
parser.add_argument('--samples', type=int, default=20, choices=[20,40],
                    help='Mixture samples per checkpoint; divisible into five CV folds.')
parser.add_argument('--seed', type=int, default=43_000_000, help='Nonnegative mixture-subsampling seed.')
args=parser.parse_args()
if args.seed < 0: parser.error('--seed must be nonnegative')
selected_cases=list(dict.fromkeys(args.case or ['llama:12','llama:39','deepseek:12','deepseek:39']))
sys.path.insert(0,str(ROOT))
UP=ROOT/'vendor/forking-fast'
sys.path.insert(0,str(UP/'otrecon/tests'))
import numpy as np
from _baseline_loader import load_baseline
from otrecon import data as od, models as active_models, cv as active_cv
from otrecon.models import MODEL_REGISTRY, MultinomialCost
from fork_microscope.live_service import reconstruct
spec=importlib.util.spec_from_file_location('audit_loader',UP/'data/loader.py')
loader=importlib.util.module_from_spec(spec);spec.loader.exec_module(loader)
base=load_baseline()
report={'created_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'python':sys.version.split()[0],
        'upstream_commit':subprocess.check_output(['git','-C',str(UP),'rev-parse','HEAD'],text=True).strip(),
        'upstream_clean':not subprocess.check_output(['git','-C',str(UP),'status','--porcelain'],text=True).strip(),
        'strict_ruptures':True,'schema':'fork-reconstruction-audit-v1',
        'settings':{'cases':selected_cases,'max_checkpoints':args.checkpoints,'samples':args.samples,'seed':args.seed},
        'scope':'selected_public_stores' if args.case else 'all_public_stores',
        'cases':[], 'checks':[],
        'limitations':['Reconstruction agreement, not full paper replication or real model execution.',
                       'Synthetic live-record adapter transports public outcome labels, not original continuation text.',
                       'No validation of sampling accuracy, uncertainty coverage, causal claims or cost savings.']}
report['application_commit']=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
report['source_hashes']={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in [Path(__file__).resolve(), ROOT/'src/fork_microscope/live_service.py',
                                    ROOT/'src/fork_microscope/sampling.py']}

report['runtime_sources']={}
for module in [od, active_models, active_cv]:
    loaded=Path(module.__file__).resolve()
    pinned=UP/'otrecon/otrecon'/loaded.name
    actual=hashlib.sha256(loaded.read_bytes()).hexdigest()
    expected_hash=hashlib.sha256(pinned.read_bytes()).hexdigest()
    if actual != expected_hash:
        raise RuntimeError(f'Loaded {module.__name__} differs from the pinned upstream source. Reinstall the pinned dependencies.')
    report['runtime_sources'][module.__name__]={'sha256':actual,'pinned_source':str(pinned.relative_to(ROOT)),
                                               'matches_pinned':True}
assert report['upstream_clean']
assert report['upstream_commit']=='d32fed8d4162a4888291c4b3a38b059727c85a41'

def add(name,**details):
    report['checks'].append({'name':name,'passed':True,**details});print(name,details,flush=True)
def err(a,b):
    a,b=np.asarray(a),np.asarray(b);assert a.shape==b.shape
    return float(np.max(np.abs(a-b))) if a.size else 0.0

manifest=json.loads((UP/'data/MANIFEST.json').read_text())
selected_files={f's200/{track}/row{int(row):03d}.json.gz' for track,row in (c.split(':') for c in selected_cases)}
files={k:v for k,v in manifest['files'].items() if not args.case or k in selected_files}
assert not args.case or set(files)==selected_files
for rel,info in files.items():
    assert hashlib.sha256((UP/'data'/rel).read_bytes()).hexdigest()==info['sha256']
add('released_store_hashes',files=len(files))
for rel in files:
    rec=loader.load_store(str(UP/'data'/rel));assert loader.matches_recorded(rec)
add('selected_weighted_reference_curves_recompute' if args.case else 'all_released_weighted_reference_curves_recompute',files=len(files))

def direct_record(pos,draws,categories):
    n=draws.shape[1]
    return {'categories':categories,'sampling_design':'position_mixture_v1','base':{'finish_reason':'stop'},
      'config':{'cont_temperature':1},'positions':[{'t':int(t),'samples':n,'retained_mass':1.0} for t in pos],
      'branches':[{'t':int(t),'draw_indices':list(range(n)),'answers':[categories[i] for i in row],
                   'observations':[{'label':categories[i],'stop_reason':'eos'} for i in row]} for t,row in zip(pos,draws)]}

# Released-data outcome sequences transported through the live schema to isolate integration.
# Deliberately sparse across each source trace: not the paper's full evaluation grid.
for case in selected_cases:
    track,row=case.split(':');row=int(row)
    rec=loader.load_store(str(UP/f'data/s200/{track}/row{row:03d}.json.gz'))
    pos,draws,diag=od.mixture_draws(rec,np.arange(5),5,n_total=args.samples,seed_base=args.seed)
    assert diag['exhausted_fallbacks']==0
    ref_pos,ref_draws,ref_diag=base.data.mixture_draws(rec,np.arange(5),5,n_total=args.samples,seed_base=args.seed)
    assert pos==ref_pos and diag==ref_diag
    np.testing.assert_array_equal(draws,ref_draws)
    chosen=np.unique(np.linspace(0,len(pos)-1,min(args.checkpoints,len(pos))).astype(int))
    x=np.asarray(pos,float)[chosen];d=draws[chosen];K=5;n=args.samples
    app=reconstruct(direct_record(x,d,rec['categories']),n,'cv',0)
    params,scores=base.cv.cv_select('M5a_segkernel',d,x,n,K,n_folds=5)
    assert app['parameters']==params
    assert app['cv_candidates']==len(scores)
    counts=np.stack([np.bincount(v,minlength=K) for v in d])
    np.testing.assert_array_equal(app['raw'],counts/n)
    baseline=base.models.MODEL_REGISTRY['M5a_segkernel']();baseline.fit(x,counts,n,params)
    support=np.arange(int(x[0]),int(x[-1])+1,dtype=float)
    np.testing.assert_array_equal(app['support'],support)
    pred=baseline.predict(support)
    e=err(app['smoothed'],pred);assert e==0
    score_error=abs(app['best_cv_score']-max(scores.values()));assert score_error==0
    expected=[{'left':int(x[e-1]),'right':int(x[e]),'midpoint':float((x[e-1]+x[e])/2)} for e in baseline.bkps[:-1]]
    assert app['boundaries']==expected
    low,high=baseline.credible_band(support,.9)
    low_error=err(app['low'],low);high_error=err(app['high'],high)
    assert low_error==0 and high_error==0
    # Native outputs and the exact shared input make each plotted comparison reproducible.
    report['cases'].append({'case':case,'source':f'vendor/forking-fast/data/s200/{track}/row{row:03d}.json.gz',
        'source_sha256':files[f's200/{track}/row{row:03d}.json.gz']['sha256'],
        'categories':rec['categories'],'positions':x.astype(int).tolist(),'draws':d.tolist(),
        'counts':counts.astype(int).tolist(),'mixture_diagnostics':diag,
        'record_adapter':'synthetic completed observations; no generated-text verification',
        'application':app,
        'upstream':{'support':support.astype(int).tolist(),'raw':(counts/n).tolist(),
                    'parameters':params,'best_cv_score':float(max(scores.values())),
                    'smoothed':pred.tolist(),'low':low.tolist(),'high':high.tolist(),'boundaries':expected}})
    # Independent algebra: prior + Gaussian-weighted counts; no cross-segment pooling.
    manual=np.empty_like(pred)
    for j,t in enumerate(support):
        segment=next(i for i,(lo,hi) in enumerate(baseline.seg_bounds_tok) if lo<t<=hi)
        a,b=baseline.seg_slices[segment];w=np.exp(-(t-x[a:b])**2/(2*params['h']**2))
        alpha=np.full(K,1/K)+w@counts[a:b];manual[j]=alpha/alpha.sum()
    me=err(pred,manual);assert me<1e-14
    add('released_data_live_vs_pristine_baseline',track=track,row=row,checkpoints=len(x),draws=n,
        parameters=params,cv_candidates=len(scores),cv_score_error=score_error,point_max_error=e,
        manual_kernel_max_error=me,low_max_error=low_error,high_max_error=high_error,boundaries=expected)

# Analytic segment likelihood, then exhaustive legal partitions vs PELT on a small example.
c=np.array([[9,1],[10,0],[9,1],[1,9],[0,10],[1,9]],float)
cost=MultinomialCost().fit(c)
for a in range(len(c)):
    for b in range(a+1,len(c)+1):
        pooled=c[a:b].sum(0);nz=pooled[pooled>0]
        assert abs(cost.error(a,b)-float(-(nz*np.log(nz/nz.sum())).sum()))<1e-12
pen=2.;partitions=[]
for size in range(0,len(c)):
    for cuts in itertools.combinations(range(1,len(c)),size):
        ends=(0,)+cuts+(len(c),)
        if min(np.diff(ends))<2:continue
        value=sum(cost.error(a,b) for a,b in zip(ends[:-1],ends[1:]))+pen*len(cuts)
        partitions.append((value,list(ends[1:])))
best=min(partitions)
m=MODEL_REGISTRY['M5a_segkernel']();m.fit(np.arange(len(c)),c,10,{'variant':'mult','pen':pen,'h':2.})
assert m.bkps==best[1]
add('multinomial_cost_and_exhaustive_pelt_check',breakpoints=m.bkps,objective=best[0],legal_partitions=len(partitions))


args.output.parent.mkdir(parents=True,exist_ok=True)
report['passed']=True
args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
print(args.output)
