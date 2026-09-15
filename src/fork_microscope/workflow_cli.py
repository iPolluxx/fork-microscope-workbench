"""Thin authenticated client; no GPU dependencies required for CLI requests."""
import json
import os
from pathlib import Path
import urllib.request
import urllib.error
import urllib.parse

class Client:
    def __init__(self,url,token=None):
        parts=urllib.parse.urlsplit(url)
        if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('','/'):
            raise ValueError('Use a worker origin without credentials or a path.')
        if parts.scheme=='http' and parts.hostname not in ('127.0.0.1','localhost','::1'):raise ValueError('Remote worker credentials require HTTPS. Use an SSH tunnel for HTTP localhost.')
        self.url=url.rstrip('/');self.token=token or os.environ.get('FORK_WORKER_TOKEN','')
    def request(self,path,payload=None):
        headers={'Content-Type':'application/json'}
        if self.token:headers['Authorization']='Bearer '+self.token
        req=urllib.request.Request(self.url+'/api/live/'+path,data=None if payload is None else json.dumps(payload,allow_nan=False).encode(),headers=headers)
        # Never forward a bearer token through an HTTP redirect.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        try:
            with urllib.request.build_opener(NoRedirect).open(req,timeout=60) as response:return json.load(response)
        except urllib.error.HTTPError as exc:
            try:message=json.load(exc).get('error','Worker request failed.')
            except (ValueError,AttributeError):message='Worker request failed.'
            raise ValueError(message) from None
    def export(self,path,destination):
        from fork_microscope.investigation_bundle import validate_bundle, canonical
        bundle=self.request(path);validate_bundle(bundle)
        dest=Path(destination);tmp=dest.with_suffix(dest.suffix+'.tmp');tmp.write_bytes(canonical(bundle));tmp.replace(dest)
        return {'file':str(dest),'run_ids':bundle['manifest']['run_ids'],'lens_ids':bundle['manifest']['lens_ids']}

def parser(sub):
    p=sub.add_parser('investigation',help='Run or retrieve a complete investigation on a connected worker')
    p.add_argument('--worker',default=os.environ.get('FORK_WORKER_URL','http://127.0.0.1:8767'))
    actions=p.add_subparsers(dest='operation',required=True)
    q=actions.add_parser('start');q.add_argument('config');q.add_argument('--request-id',required=True,help='Stable ID: reuse on retries, change for a new experiment')
    actions.add_parser('list')
    actions.add_parser('settings',help='Print all workflow fields and their effects; no connection needed')
    actions.add_parser('validate',help='Check configuration syntax without compute').add_argument('config')
    for action in ('status','cancel','resume'):
        actions.add_parser(action).add_argument('id')
    q=actions.add_parser('export');q.add_argument('id');q.add_argument('output')
    q=actions.add_parser('export-run');q.add_argument('id');q.add_argument('output')
    q=actions.add_parser('import');q.add_argument('file')

def execute(args):
    from fork_microscope.investigation_bundle import identifier,validate_bundle,MAX_IMPORT_BYTES
    if args.operation=='settings':
        print(json.dumps(settings_reference(),indent=2));return
    if args.operation=='validate':
        from fork_microscope.investigation_workflow import validate_config
        validate_config(json.loads(Path(args.config).read_text()))
        print(json.dumps({'valid':True,'scope':'Configuration syntax only; no worker contacted or compute started. Model and lens readiness still require worker checks.'},indent=2));return
    client=Client(args.worker)
    if args.operation=='start':
        from fork_microscope.investigation_workflow import validate_config
        c=validate_config(json.loads(Path(args.config).read_text()))
        result=client.request('workflow-start',{'request_id':args.request_id,'config':c})
    elif args.operation=='list':result=client.request('workflows')
    elif args.operation=='import':
        p=Path(args.file)
        if p.stat().st_size>MAX_IMPORT_BYTES:raise ValueError('Bundle exceeds 64 MB.')
        bundle=json.loads(p.read_text());validate_bundle(bundle);result=client.request('import',bundle)
    else:
        id=identifier(args.id)
        if args.operation in ('export','export-run'):
            result=client.export(('workflow-export' if args.operation=='export' else 'bundle-export')+'?id='+id,args.output)
        elif args.operation=='status':result=client.request('workflow?id='+id)
        else:result=client.request('workflow-'+args.operation,{'id':id})
    print(json.dumps(result,indent=2,allow_nan=False))


def settings_reference():
    """Static configuration help. No model imports, network access, or execution."""
    rows = [
        ('model.model_id','nonempty string','Hub repository or worker-local native model directory. A locator, not a cloud API endpoint.'),
        ('model.revision','nonempty string; main/master/latest rejected','Use an immutable Hub commit or the expected local content identity. Other moving branch names are not detected by syntax validation; the agent must pin them.'),
        ('model.device','auto | cpu | cuda','Device for loading. auto chooses an available device; it does not guarantee enough memory. cuda requires a CUDA worker.'),
        ('model.batch_size','integer 1–128','Number of continuations processed concurrently. Controls memory/throughput, not total samples or statistical confidence.'),
        ('base.prompt','1–16000 characters after trimming','The user question/context supplied to the model. Actual token count depends on the tokenizer and chat template.'),
        ('base.answers','1–32 nonempty single-line strings, ≤200 characters each; unique ignoring case/whitespace; Other reserved','Outcome texts to recognize in completed replies. Multiple matches, unmatched or incomplete replies are Other. Matching normalizes case/whitespace and uses word edges. It is not correctness grading.'),
        ('base.mode','chat | base','chat applies the model chat template; base tokenizes the supplied text with special tokens, without a chat template. Inspect saved prompt IDs for exact conditioning.'),
        ('base.max_tokens','integer 8–4096','Maximum newly generated tokens for the original response. A capped original response stops scanning; it is not silently treated as complete.'),
        ('base.seed','integer 0–2147483647','Seed recorded for original-response generation, which is greedy in this workflow; changing it is not a way to sample alternative base responses. Reproducibility also depends on model, runtime, device and settings.'),
        ('scan.start','integer 0–4095','First checkpoint on the GENERATED RESPONSE, not the user prompt. At checkpoint t, preserve the first t response tokens and branch at response index t.'),
        ('scan.end','null or integer start+1–4095','Upper bound clamped to the last available response index. null means through the response. Initial scan includes only regular-grid positions; an off-grid endpoint is not appended.'),
        ('scan.stride','integer 1–128','Distance between checkpoints. Smaller spacing gives finer coverage at greater cost. At least two actual checkpoints are required.'),
        ('scan.samples','integer 5–512','Total fresh continuation draws per checkpoint, allocated across retained branch tokens; not samples per branch. More draws reduce sampling uncertainty but do not improve position spacing.'),
        ('scan.cont_max','integer 1–4096','Maximum generated continuation tokens after the forced branch token per draw. Longer caps cost more but reduce truncation. This is not number of layers or checkpoints.'),
        ('scan.temperature','finite number 0.05–2','Temperature for continuation generation after the selected branch. Branch selection itself uses temperature-1 next-token probabilities.'),
        ('scan.top_k','integer 1–50','Maximum next-token branch candidates considered at each checkpoint before applying the probability threshold. This is NOT J-lens display top-k or continuation-generation top-k.'),
        ('scan.threshold','finite number 0–1','Minimum next-token probability for a candidate branch. Retained candidates are renormalized; excluded probability mass is not measured. A threshold leaving no candidates prevents collection.'),
        ('scan.seed','integer 0–2147483647','Seed for branch allocation and continuation sampling. Source records preserve derived seeds. Separate from base.seed.'),
        ('refinement.max_rounds','integer 0–8','Maximum follow-up runs after the initial scan. 0 disables refinement. It may stop earlier if no eligible gap remains or the budget is insufficient.'),
        ('refinement.stride','integer 1–128','Spacing within the selected interval. Both endpoints are included, even if off-grid. Only gaps wider than this stride are eligible.'),
        ('refinement.samples','integer 5–512','Fresh draws at each refinement checkpoint, including repeated endpoints. These are additional observations; earlier counts are not reused as new samples.'),
        ('refinement.min_tvd','finite number 0–1','Minimum adjacent raw-distribution total variation distance to consider. This is a descriptive selection threshold, NOT a p-value, confidence level, or guarantee of a fork.'),
        ('lens.profile','string matching a compatible named worker profile','Query GET /api/live/lens-options for profile IDs, model compatibility, installation and layer count. Profiles are model-specific; arbitrary filenames and local profiles are not accepted by this automatic workflow.'),
        ('lens.layers','1–16 unique integers, each 0–999','Zero-based decoder layer indices. The loaded model and fitted lens impose tighter bounds; the final decoder block is not a fitted source layer. More layers increase capture/projection work.'),
        ('lens.before','integer 0–16','Inspection positions before the selected pair’s FIRST DIFFERING TOKEN. Clipped at zero. Not an offset from the sampling checkpoint.'),
        ('lens.after','integer 0–16','Inspection positions after the first differing token. Clipped to the shorter path. The divergence position itself is included.'),
        ('lens.top_k','integer 2–30','Number of ranked vocabulary tokens displayed per readout. Changes output detail; does not sample more continuations or add layers.'),
        ('lens.inspection_backend','optional native or nnsight; default native','Read-only capture backend for the same J-lens method. NNsight requires the pinned optional worker package; missing support fails before model loading or sampling. No NDIF connection or speedup is implied. Query GET /api/live/lens-options.'),
        ('limits.max_seconds','integer 1–1000000000','Time allowance including loading, generation and lens replay. Stopping is cooperative; an in-flight operation may overrun. Restart downtime can consume allowance. NOT a provider billing cap.'),
        ('limits.max_samples','integer 1–1000000000','Maximum reserved continuation draws across scan and refinement. Original-response generation and lens replay are not continuation draws.'),
        ('limits.max_generated_tokens','integer 1–1000000000','Maximum reserved generated-token allowance: original cap plus each phase’s draws × continuation cap. Reservations are not refunded for short output or interrupted attempts. Does not count replayed/prompt tokens, which still cost compute.')
    ]
    return dict(schema='fork-workflow-settings-reference-v1',
        configuration_keys=['model','base','scan','refinement','lens','limits'],
        defaults='All fields are required except lens.inspection_backend, which defaults to native. lens may be null. Examples are configurations, not universal recommendations.',
        fields=[dict(path=p,allowed=a,effect=e) for p,a,e in rows],
        fixed_behavior=dict(matching='answer_text_anywhere_v1',reconstruction='cv',dense_reference=False,
            branch_temperature=1,continuation_top_p=1,continuation_top_k=0,
            refinement='Largest adjacent raw TVD in the latest eligible completed fit; one interval per round.',
            lens='One contrasting pair per workflow, searching newest runs first; most balanced eligible checkpoint, then first contrasting draws in draw order.'),
        validation='Syntax validation is offline. Worker checks model fit, template, context length, exact revision, lens compatibility and actual positions when executing.',
        manual_operations='Explicit multiple passes, offsets, dense reference, fixed tuning, manual lens positions, local lens files and text/activation interventions use the separate manual APIs. Do not add their fields to this workflow config.')
