# generated: Codex — durable exact-token response selection extends the existing service.
"""One attached model, cancellable collection jobs, and auditable saved results."""
from __future__ import annotations
import gc
import importlib.metadata
import json
import math
from pathlib import Path
import threading
import time
import uuid
import numpy as np
from forking_paths.config import ForkingConfig
from forking_paths.resample import enumerate_branches_at
from otrecon import data as od
from otrecon.cv import cv_select
from otrecon.models import MODEL_REGISTRY
from fork_microscope.outcome_readout import validate_answers
from fork_microscope.sampling import pass_plan, allocation, position_draws
from otrecon.metrics import tv_to_gt, band_coverage, heldout_loglik

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "live-runs"
CATS = ["A", "B", "C", "D", "Other"]


def integer(value, name, lo, hi):
    if type(value) is not int or not lo <= value <= hi:
        raise ValueError(f"{name} must be a whole number from {lo} to {hi}.")
    return value


def real(value, name, lo, hi):
    if type(value) not in (float, int) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}.")
    return value


def exact(obj, fields):
    if type(obj) is not dict or set(obj) != set(fields.split()):
        raise ValueError("Unexpected or missing fields.")


def grid_plan(c, last):
    plan = pass_plan(c, last)
    grids = {p['id']:p['positions'] for p in plan}
    grids['dense'] = list(range(min(p['positions'][0] for p in plan), max(p['positions'][-1] for p in plan)+1))
    return grids


def reconstruct(record, n, tuning, seed):
    warnings = []
    K = len(record["categories"])
    if record.get('sampling_design') == 'position_mixture_v1':
        positions, draws = position_draws(record)
        idxs = positions
        if draws.shape != (len(positions), n): raise ValueError("Saved draw count differs from requested reconstruction count.")
        weighted = od.counts_from_draws(draws,K,0,n)/n
        diag = dict(n_total=n,n_positions=len(positions),exhausted_fallbacks=0,all_collected_draws_used=True)
        capped = sum(o['stop_reason']=='length' for b in record['branches'] for o in b['observations'])
        total = draws.size
        unparsed=sum(o.get('label')=='Other' and o['stop_reason']!='length' for b in record['branches'] for o in b['observations'])
        if unparsed: warnings.append(f'{unparsed}/{total} completed continuations have no parsed answer; Other is not an answer choice.')
        if capped/total > .1:
            warnings.append(f'{capped}/{total} continuations reached the token cap. Reconstruction withheld; increase the cap.')
        if record['base'].get('finish_reason') != 'stop':
            warnings.append('The fixed base response did not finish. Inspect it before interpreting outcomes.')
        if n < 20: warnings.append('Fewer than 20 draws per checkpoint: this is a small-sample diagnostic, not a reliability guarantee.')
        masses = [p['retained_mass'] for p in record['positions']]
        if min(masses)<.95: warnings.append('Some checkpoints retain less than 95% of next-token probability mass; omitted branches are excluded.')
        if record['config'].get('cont_temperature',1)!=1:
            warnings.append('Branch weights use temperature 1; continuation temperature differs.')
        if capped/total > .1:
            return dict(positions=positions,weighted=weighted.tolist(),raw=weighted.tolist(),support=[],smoothed=[],low=[],high=[],boundaries=[],parameters=None,tuning='withheld',cv_candidates=0,best_cv_score=None,mixture_diagnostics=diag,warnings=warnings,fit_status='withheld',segmentation_enabled=False,bands_kind=None)
    else:
        idxs, weighted = od.weighted_o_t(record)
        positions, draws, diag = od.mixture_draws(record, np.arange(K), K, n_total=n, seed_base=seed)
        if diag['exhausted_fallbacks']: raise RuntimeError('Mixture sampling exhausted a branch unexpectedly.')
        warnings.append('Legacy per-branch record: the fit uses a subsample; weighted markers use all branch observations.')
    if len(positions)<4:
        warnings.append('Fewer than four checkpoints: segmentation and cross-validation are disabled; fixed smoothing only.')
        tuning='fixed'
    x = np.asarray(positions, float)
    params, scores = (cv_select("M5a_segkernel", draws, x, n, K, n_folds=5)
        if tuning == "cv" else ({"variant":"mult", "pen":64.0, "h":32.0}, {}))
    counts = od.counts_from_draws(draws, K, 0, n)
    model = MODEL_REGISTRY["M5a_segkernel"]()
    model.fit(x, counts, n, params)
    support = np.arange(positions[0], positions[-1]+1)
    pred = model.predict(support.astype(float))
    low, high = model.credible_band(support.astype(float), .9)
    if not np.isfinite(pred).all() or not np.allclose(pred.sum(axis=1), 1):
        raise RuntimeError("Invalid reconstruction probabilities.")
    boundaries = [dict(left=positions[e-1], right=positions[e], midpoint=(positions[e-1]+positions[e])/2)
        for e in model.bkps[:-1]]
    return dict(positions=idxs, weighted=weighted.tolist(), raw=(counts/n).tolist(),
        support=support.tolist(), smoothed=pred.tolist(), low=low.tolist(), high=high.tolist(),
        boundaries=boundaries, parameters=params, tuning=tuning,
        cv_candidates=len(scores), best_cv_score=max(scores.values()) if scores else None,
        mixture_diagnostics=diag,warnings=warnings,fit_status='complete',
        segmentation_enabled=len(positions)>=4,bands_kind='model_based_dirichlet_marginal')


def compare(reference, curve, samples, seed):
    K = len(reference['categories'])
    if reference.get('sampling_design') == 'position_mixture_v1':
        positions, draws = position_draws(reference)
        weighted = od.counts_from_draws(draws,K,0,samples)/samples
    else:
        positions, weighted = od.weighted_o_t(reference)
        _, draws, _ = od.mixture_draws(reference, np.arange(K), K, n_total=samples, seed_base=seed)
    ix = np.array([positions.index(t) for t in curve["support"]])
    pred = np.asarray(curve["smoothed"])
    return dict(mean_tv=float(tv_to_gt(pred, weighted[ix]).mean()),
        mean_log_likelihood=heldout_loglik(pred, draws[ix], 0),
        empirical_band_coverage=float(band_coverage(np.asarray(curve["low"]),np.asarray(curve["high"]),weighted[ix]).mean()),
        compared_positions=len(ix))


class Cancelled(Exception):
    pass


class LiveService:
    def __init__(self, workspace_root=None):
        from fork_microscope.workspace_store import WorkspaceStore
        self.workspace = WorkspaceStore(workspace_root or ROOT / "workspace-data")
        self.workspace.recover()
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.model = self.base = self.question = None
        self.response_id = None
        self.job = dict(status="idle", phase="Attach an open-weight model to begin.", completed=0, total=0)
        self.base_config = None
        self.lineage = None
        self._runtime = None
        self.workflow_owner = None
        self.workflow_deadline = None
        RUNS.mkdir(exist_ok=True)

    def runtime_metadata(self):
        if self._runtime is None:
            import os
            import torch
            cuda = torch.cuda.is_available()
            try:
                memory = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 1024**3
            except (ValueError, OSError, AttributeError):
                memory = None
            self._runtime = dict(cuda_available=cuda,
                gpu_name=torch.cuda.get_device_name(0) if cuda else None,
                gpu_memory_gb=round(torch.cuda.get_device_properties(0).total_memory/1024**3,1) if cuda else None,
                system_memory_gb=round(memory, 1) if memory is not None else None)
        return dict(self._runtime)

    def status(self):
        with self.lock:
            return dict(job=dict(self.job), model=self.model.info if self.model else None,
                base=self.base_metadata() if self.base else None, runtime=self.runtime_metadata())

    def base_metadata(self):
        from fork_microscope.outcome_readout import inspect_base
        return dict(question=self.question, text=self.model.decode(self.base.gen_ids),
            tokens=[self.model.tokenizer.decode([x]) for x in self.base.gen_ids],
            length=len(self.base.gen_ids), finish_reason=self.base.finish_reason,
            config=self.base_config, readout=inspect_base(self.model,self.base,(self.question or {}).get('answers'), rule=(self.base_config or {}).get('outcome_rule')),
            top_token_probabilities=[float(np.exp(x[0])) for x in self.base.topk_logprobs])

    def progress(self, phase, completed=0, total=0):
        with self.lock:
            self.job.update(phase=phase, completed=completed, total=total, activity=None,
                progress_unit='tokens' if phase.startswith('Replaying saved tokens') else 'continuations')

    def activity(self, value):
        """Within-operation telemetry; never replaces completed sample counts."""
        with self.lock:
            self.job['activity'] = dict(value, updated=time.time())

    def check(self):
        if self.stop.is_set() or (self.workflow_deadline is not None and time.monotonic() >= self.workflow_deadline):
            raise Cancelled()

    def start(self, action, payload, owner=None, job_id=None):
        with self.lock:
            if self.workflow_owner is not None and owner != self.workflow_owner:
                raise ValueError("An investigation owns this worker. Cancel or finish it before starting another model job.")
            if self.job["status"] == "running":
                raise ValueError("A job is already running. Wait or stop it first.")
            if action not in ("load", "base", "run", "unload", "refine", "batch", "investigate", "lens", "patch", "select_response"):
                raise ValueError("Unknown action.")
            # Validate synchronously before launching jobs.
            if action == "load":
                exact(payload, "model_id revision device batch_size")
                for key in ("model_id", "revision"):
                    if not isinstance(payload[key], str) or not payload[key].strip() or len(payload[key])>500:
                        raise ValueError("Enter a model ID/path and revision.")
                if payload["device"] not in ("auto", "cpu", "cuda"):
                    raise ValueError("Choose auto, CPU or CUDA.")
                integer(payload["batch_size"], "Batch size", 1, 128)
                runtime = self.runtime_metadata()
                if payload['device'] == 'cuda' and not runtime['cuda_available']:
                    raise ValueError('No CUDA GPU is available on this runtime. Open the app on your GPU worker, or explicitly select CPU for a compatible smaller model.')
            elif action == "base":
                if not self.model: raise ValueError("Attach a model first.")
                if 'prompt' in payload:
                    extra = set(payload) & {'temperature','outcome_rule','messages'}
                    exact(payload, 'prompt answers mode max_tokens seed' + ''.join(' ' + k for k in extra))
                    if 'messages' in payload:
                        from fork_microscope.investigation_records import validate_messages
                        validate_messages(payload['messages'])
                        if payload['mode'] != 'chat': raise ValueError('Conversation history requires chat mode.')
                    if 'temperature' in payload: real(payload['temperature'], 'Temperature', 0, 2)
                    if 'outcome_rule' in payload:
                        from fork_microscope.investigation_records import validate_rule
                        payload = dict(payload, outcome_rule=validate_rule(payload['outcome_rule']))
                        if payload['outcome_rule']['answers'] != payload['answers']: raise ValueError('Outcome rules and answers differ.')
                    if not isinstance(payload['prompt'], str) or not 1 <= len(payload['prompt'].strip()) <= 16000:
                        raise ValueError('Enter a prompt (up to 16,000 characters).')
                    payload = dict(payload, answers=validate_answers(payload['answers']))
                else:
                    exact(payload, "question choices mode max_tokens seed")
                    if not isinstance(payload["question"], str) or not 1<=len(payload["question"].strip())<=16000:
                        raise ValueError("Enter a question (up to 16,000 characters).")
                    if type(payload["choices"]) is not list or len(payload["choices"]) != 4 or any(not isinstance(x,str) or not x.strip() or len(x)>4000 for x in payload["choices"]):
                        raise ValueError("Enter four nonempty answer choices.")
                if payload["mode"] not in ("chat", "base"): raise ValueError("Choose chat or base mode.")
                integer(payload["max_tokens"], "Base cap", 8, 4096)
                integer(payload["seed"], "Seed", 0, 2**31-1)
            elif action == "select_response":
                exact(payload, "response_id")
                saved = self.response(payload["response_id"])
                from fork_microscope.model_preflight import same_model_identity
                if not self.model or not same_model_identity(self.model.info, saved["model"]):
                    raise ValueError("Attach the exact response model and revision before scanning.")
            elif action == "lens":
                payload = self.lens_plan(payload)
            elif action == "patch":
                payload = self.patch_plan(payload)
            elif action == "investigate":
                payload = self.investigation_plan(payload)
            elif action == "refine":
                plan=self.refinement_plan(payload)
                if not self.model:raise ValueError('Attach the source model in Technical controls first, or export this job for a GPU worker.')
                from fork_microscope.model_preflight import same_model_identity
                if not same_model_identity(self.model.info,plan['model']):
                    raise ValueError('Attach the exact source model revision before refining.')
                payload=plan
            elif action == "batch":
                if not self.model: raise ValueError("Attach a model before starting a prompt set.")
                payload=self.workspace.prepare_batch(payload,self.model.info)
            elif action == "run":
                self.estimate(payload)  # Checks base, complete config and context allowance.
            else:
                exact(payload, "")
            self.stop.clear()
            self.job = dict(id=job_id or uuid.uuid4().hex, status="running", action=action,
                phase=f"Starting {action}…", completed=0,total=0, started=time.time())
            job_id = self.job["id"]
            if action == "batch":
                payload['job_id']=job_id
                self.job['batch_id']=payload['id']
                self.workspace.save_batch(payload)
            threading.Thread(target=self._execute, args=(action,payload), daemon=True).start()
            return dict(job_id=job_id, **({'batch_id':payload['id']} if action=='batch' else {}))

    def _execute(self, action, p):
        try:
            if action == 'load':
                from fork_microscope.model_preflight import preflight_model
                self.progress('Inspecting model metadata and worker memory…')
                inspected=preflight_model(p,self.runtime_metadata())
                if not inspected['can_load']:raise ValueError(' '.join(inspected['blockers']))
                self.check()
            if action in ("unload", "load"):
                self.base = self.question = self.base_config = self.lineage = self.response_id = None
                self.model = None
                gc.collect()
                import torch
                if torch.cuda.is_available(): torch.cuda.empty_cache()
                if action == "load":
                    from fork_microscope.live_model import AttachedModel
                    self.progress("Loading tokenizer and weights…")
                    self.model = AttachedModel(p["model_id"],p["revision"],p["device"],p["batch_size"],progress=self.progress,
                        activity=self.activity, check=self.check)
                    self.check()
            elif action == "select_response":
                self.select_response(p["response_id"])
            elif action == "lens":
                self.execute_lens(p)
            elif action == "patch":
                self.execute_patch(p)
            elif action == "investigate":
                self.execute_investigation(p)
            elif action == "base":
                self.generate_base(p)
            elif action == "batch":
                self.execute_batch(p)
            elif action == "refine":
                from fork_microscope.replay_trace import replay_saved_trace
                self.base=self.question=self.base_config=None
                self.lineage=None
                self.response_id=p.get('source_response_id')
                base=replay_saved_trace(self.model,p['base'],p['model'],progress=self.progress)
                self.check()
                self.base=base;self.base_config=p['base_config']
                self.question=(dict(question=self.base_config['prompt'],answers=self.base_config['answers'],matching='answer_text_anywhere_v1') if 'prompt' in self.base_config else dict(question=self.base_config['question'],choices=self.base_config['choices']))
                self.lineage=dict(p['lineage'],exact_ids_verified=True)
                self.collect(p['run'])
            else:
                self.collect(p)
            with self.lock:
                self.job.update(status="complete",phase=f"{action.capitalize()} complete",finished=time.time())
                if action == 'base':
                    self.job['phase'] = ('Response generated — review before scanning.' if self.base.finish_reason == 'stop'
                        else 'Response reached its token limit — generate again before scanning.')
                if action in ("run","refine"): self.job["completed"]=self.job["total"]
        except Cancelled:
            with self.lock: self.job.update(status="cancelled",phase="Stopped at a sampling boundary. Any saved partial collection is retained.",finished=time.time())
        except Exception as exc:
            with self.lock: self.job.update(status="error",phase=str(exc)[:1500],finished=time.time())

    # generated: Codex — investigations share the model's existing job lock.
    def patch_options(self):
        from fork_microscope.activation_patching import options
        with self.lock:
            return options(self.model)

    def patch_plan(self, request):
        from fork_microscope.activation_patching import build_plan
        with self.lock:
            if self.job['status'] == 'running':
                raise ValueError('Wait for the current model job before preparing a patch experiment.')
            if not self.model:
                raise ValueError('Connect compute and load the exact source model to run a patch experiment.')
            if type(request) is not dict or not isinstance(request.get('source_run_id'), str):
                raise ValueError('Choose a saved source run.')
            return build_plan(self.model, self.result(request['source_run_id'], raw=True), request, self.investigation)

    def execute_patch(self, plan):
        from fork_microscope.activation_patching import run
        from fork_microscope.model_preflight import same_model_identity
        if not same_model_identity(self.model.info, plan['model']):
            raise ValueError('Attached model changed after preparing the patch plan.')
        folder = RUNS.parent / 'investigations'
        folder.mkdir(exist_ok=True)
        identifier = self.job['id']
        path = folder / (identifier + '.json')
        plan = dict(plan, id=identifier)
        current = dict(plan, status='running', created=time.time(), observations=[])
        def save(value):
            nonlocal current
            current = value
            self.save(path, value)
        save(current)
        with self.lock:
            self.job['investigation_id'] = identifier
        try:
            run(self.model, plan, self.check, self.progress, save)
        except Exception as exc:
            save(dict(current, status='cancelled' if isinstance(exc, Cancelled) else 'error', error=str(exc), finished=time.time()))
            raise

    def lens_options(self):
        from fork_microscope.lens_integration import options
        with self.lock:
            return options(self.model)

    def lens_plan(self, request):
        from fork_microscope.lens_integration import build_plan
        with self.lock:
            if self.job['status'] == 'running':
                raise ValueError('Wait for the current model job before preparing a lens readout.')
            if not self.model:
                raise ValueError('Attach the exact source model to inspect its internal states.')
            if type(request) is not dict or not isinstance(request.get('source_run_id'), str):
                raise ValueError('Choose a saved source run.')
            return build_plan(self.model, self.result(request['source_run_id'], raw=True), request, self.investigation)

    def execute_lens(self, plan):
        from fork_microscope.lens_integration import run
        from fork_microscope.model_preflight import same_model_identity
        if not same_model_identity(self.model.info, plan['model']):
            raise ValueError('Attached model changed after preparing the lens plan.')
        folder = RUNS.parent / 'investigations'
        folder.mkdir(exist_ok=True)
        identifier = self.job['id']
        path = folder / (identifier + '.json')
        plan = dict(plan, id=identifier)
        current = dict(plan, status='running', created=time.time(), cells=[])
        def save(value):
            nonlocal current
            current = value
            self.save(path, value)
        save(current)
        with self.lock:
            self.job['investigation_id'] = identifier
        try:
            run(self.model, plan, self.check, self.progress, save)
        except Exception as exc:
            save(dict(current, status='cancelled' if isinstance(exc, Cancelled) else 'error', error=str(exc), finished=time.time()))
            raise

    def investigation_plan(self, request):
        from fork_microscope.investigation import build_plan
        with self.lock:
            if self.job['status'] == 'running':
                raise ValueError('Wait for the current model job before preparing an investigation.')
            if not self.model:
                raise ValueError('Attach the exact source model to investigate saved evidence.')
            if type(request) is not dict or not isinstance(request.get('source_run_id'), str):
                raise ValueError('Choose a saved source run.')
            result = self.result(request['source_run_id'], raw=True)
            return build_plan(self.model, result, request)

    def execute_investigation(self, plan):
        from fork_microscope.investigation import source, run_edit, capture
        request = plan['request']
        result = self.result(request['source_run_id'], raw=True)
        base = source(self.model, result, request)
        folder = RUNS.parent / 'investigations'
        folder.mkdir(exist_ok=True)
        path = folder / (self.job['id'] + '.json')
        plan = dict(plan, id=self.job['id'], versions={p: importlib.metadata.version(p) for p in ('torch', 'transformers')})
        current = dict(plan, status='running')
        def save(data):
            nonlocal current
            current = data
            self.save(path, data)
        save(current)
        with self.lock:
            self.job['investigation_id'] = plan['id']
        try:
            if request['kind'] == 'edit':
                run_edit(self.model, base, result['base_config'].get('answers'), plan, self.check, self.progress, save)
            else:
                capture(self.model, base, plan, self.check, self.progress, save)
        except Exception as exc:
            save(dict(current, status='cancelled' if isinstance(exc, Cancelled) else 'error', error=str(exc), finished=time.time()))
            raise

    def investigations(self):
        folder = RUNS.parent / 'investigations'
        entries = []
        for path in folder.glob('*.json'):
            try:
                data = json.loads(path.read_text())
                entries.append(dict({k: data.get(k) for k in ('id', 'schema', 'status', 'request', 'created')},
                                    kind='lens' if data.get('schema') == 'fork-lens-v1' else 'patch' if data.get('schema') == 'fork-activation-patch-v1' else data.get('request', {}).get('kind')))
            except (ValueError, OSError):
                continue
        return sorted(entries, key=lambda item: item.get('created') or 0, reverse=True)

    def investigation(self, identifier):
        if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
            raise ValueError('Invalid investigation ID.')
        path = RUNS.parent / 'investigations' / (identifier + '.json')
        if not path.is_file():
            raise ValueError('No saved investigation exists for this ID.')
        return json.loads(path.read_text())

    def generate_base(self, p):
        self.lineage = None
        self.response_id = None
        self.base = self.question = self.base_config = None
        self.progress("Generating a response and recording next-token probabilities…")
        if p.get('messages'):
            if not self.model.tokenizer.chat_template: raise ValueError('The tokenizer has no chat template for conversation history.')
            ids = list(self.model.tokenizer.apply_chat_template(p['messages'] + [{'role':'user','content':p['prompt']}], tokenize=True, add_generation_prompt=True, return_dict=False))
        else:
            ids = (self.model.prompt_text(p['prompt'], p['mode']) if 'prompt' in p
            else self.model.prompt(p["question"],p["choices"],p["mode"]))
        self.context_check(len(ids)+p['max_tokens'])
        base=self.model.base_path(ids,p['max_tokens'],top_k=min(50,self.model.info['vocab_size']),seed=p['seed'], **({'temperature': p['temperature']} if 'temperature' in p else {}))
        from fork_microscope.investigation_records import make_response
        response_id = self.job.get('id') or uuid.uuid4().hex
        # Batch responses need their own identity; the batch job ID is shared.
        if self.job.get('action') == 'batch': response_id = uuid.uuid4().hex
        artifact = make_response(self.model, base, p, response_id, self.workflow_owner)
        folder = RUNS.parent / 'responses'; folder.mkdir(parents=True, exist_ok=True)
        self.save(folder / (response_id + '.json'), artifact)
        self.response_id = response_id
        self.job['response_id'] = response_id
        self.check()
        self.base,self.question,self.base_config=base,(dict(question=p['prompt'],answers=p['answers'],matching=p.get('outcome_rule',{}).get('method','answer_text_anywhere_v1')) if 'prompt' in p else dict(question=p['question'],choices=p['choices'])),p

    def response(self, response_id):
        from fork_microscope.investigation_bundle import identifier
        from fork_microscope.investigation_records import validate_response
        path = RUNS.parent / 'responses' / (identifier(response_id) + '.json')
        if not path.exists(): raise ValueError('Saved response is unavailable on this worker.')
        return validate_response(json.loads(path.read_text()))

    def responses(self, investigation_id=None):
        values = []
        for path in (RUNS.parent / 'responses').glob('*.json'):
            value = self.response(path.stem)
            if investigation_id is None or value.get('investigation_id') == investigation_id: values.append(value)
        return sorted(values, key=lambda x: x['created'])

    def select_response(self, response_id):
        from fork_microscope.replay_trace import replay_saved_trace
        saved = self.response(response_id)
        self.base = replay_saved_trace(self.model, saved['base'], saved['model'], progress=self.progress)
        self.base_config = saved['config']
        self.question = dict(question=self.base_config.get('prompt', self.base_config.get('question', '')),
            answers=self.base_config.get('answers'), matching=(saved.get('outcome_rule') or {}).get('method', 'unknown'))
        self.response_id = response_id; self.lineage = None

    def execute_batch(self, batch):
        from fork_microscope.workspace_store import scan_config
        batch['state']='running';self.workspace.save_batch(batch)
        try:
            for index,item in enumerate(batch['items']):
                self.check()
                item['state']='running';item['started_at']=time.time()
                self.workspace.save_batch(batch)
                with self.lock:
                    self.job.update(batch_item=index+1,batch_size=len(batch['items']),prompt_title=item['title'])
                    self.job.pop('result_id',None)
                try:
                    p={k:v for k,v in item['prompt_snapshot'].items() if k not in ('id','title')}
                    self.generate_base(p)
                    self.check()
                    config=scan_config(batch['scan'],len(self.base.gen_ids)-1)
                    self.estimate(config)
                    self.lineage=dict(batch_id=batch['id'],prompt_set_id=batch['set_id'],
                        prompt_set_revision=batch['set_revision'],prompt_id=item['prompt_id'])
                    item['run_id']=uuid.uuid4().hex
                    self.workspace.save_batch(batch)
                    self.collect(config,run_id=item['run_id'])
                    item['state']='complete'
                except Cancelled:
                    item['state']='cancelled';raise
                except Exception as exc:
                    item['state']='error';item['error']=str(exc)[:1500]
                finally:
                    item['finished_at']=time.time();self.workspace.save_batch(batch)
            batch['state']='complete' if all(i['state']=='complete' for i in batch['items']) else 'complete_with_errors'
            if batch['state']=='complete_with_errors':
                raise ValueError('Batch finished with item errors. Open Workspace to inspect completed runs and failed prompts.')
        except Cancelled:
            batch['state']='cancelled'
            for item in batch['items']:
                if item['state']=='pending':item['state']='cancelled'
            raise
        finally:
            self.workspace.save_batch(batch)

    def refinement_plan(self, request):
        from fork_microscope.refinement import build_plan
        if type(request) is not dict:raise ValueError('Expected refinement settings.')
        result=self.result(request.get('source_run_id',''),raw=True)
        record=result['records'].get(request.get('source_pass_id'))
        if not record:raise ValueError('Source observation record is unavailable.')
        return build_plan(result,record,request)

    def context_check(self, needed):
        limit = self.model.info["context_limit"]
        if limit and needed > limit:
            raise ValueError(f"This configuration needs up to {needed} context tokens; the model limit is {limit}. Reduce token caps.")

    def configuration(self, c):
        cfg = ForkingConfig(top_k=c["top_k"],p_thresh=c["threshold"],n_samples=c.get("samples",5),
            n0_samples=c.get("samples",5),tok_depth=len(self.base.gen_ids),cont_max_tokens=c["cont_max"],
            base_max_tokens=self.base_config["max_tokens"] if self.base_config else len(self.base.gen_ids),
            cont_temperature=c["temperature"],seed=c.get("seed",0))
        return cfg

    def branches(self, cfg, positions):
        # The base records the top 50 once; apply the user's top-k before upstream enumeration.
        from dataclasses import replace
        base = replace(self.base,topk_ids=[x[:cfg.top_k] for x in self.base.topk_ids],
            topk_logprobs=[x[:cfg.top_k] for x in self.base.topk_logprobs])
        return enumerate_branches_at(base, cfg, positions)

    def estimate(self, c):
        if not self.base or not self.model: raise ValueError("Generate a base response first.")
        if self.base.finish_reason != 'stop':
            raise ValueError('The original response hit its token limit before finishing. Increase the base token cap or revise the prompt, then generate a complete response before scanning.')
        plan = pass_plan(c,len(self.base.gen_ids)-1)
        grids = grid_plan(c,len(self.base.gen_ids)-1)
        self.context_check(len(self.base.prompt_ids)+max(grids['dense'])+1+c['cont_max'])
        cfg = self.configuration(c)
        counts = {key:len(self.branches(cfg,pos)) for key,pos in grids.items()}
        combined = sum(len(p['positions'])*p['samples'] for p in plan)
        reference = len(grids['dense'])*c['reference_samples'] if c['dense'] else 0
        return dict(grids=grids,passes=plan,branches=counts,combined_rollouts=combined,
            reference_rollouts=reference,total_rollouts=combined+reference,
            max_continuation_tokens=(combined+reference)*c['cont_max'],
            sampled_checkpoint_visits=sum(len(p['positions']) for p in plan),
            unique_checkpoints=len(set(t for p in plan for t in p['positions'])),
            dense_positions=len(grids['dense']),sampling_design='position_mixture_v1')

    def categories(self):
        return [*self.question['answers'], 'Other'] if self.question and 'answers' in self.question else CATS

    def record(self, cfg):
        return dict(meta=dict(row_id=0,**self.question,model=self.model.info["model_id"]),
            categories=self.categories(),base=dict(gen_ids=self.base.gen_ids,prompt_ids=self.base.prompt_ids,
            base_text=self.model.decode(self.base.gen_ids),token_texts=[self.model.tokenizer.decode([x]) for x in self.base.gen_ids],
            finish_reason=self.base.finish_reason),config=cfg.to_dict(),branches=[])

    def collect(self, c, run_id=None):
        from fork_microscope.outcome_readout import inspect_continuation
        estimate = self.estimate(c)
        cfg = self.configuration(c)
        run_id = run_id or self.job['id']; folder = RUNS/run_id; folder.mkdir()
        records,curves,phase_costs = {},{},{}
        done=0; total=estimate['total_rollouts']; collection_started=time.time()
        with self.lock: self.job.update(collection_started=collection_started, generated_tokens=0)
        metadata = dict(source_response_id=self.response_id,outcome_rule=(self.base_config or {}).get('outcome_rule'),lineage=self.lineage,id=run_id,schema_version=2,sampling_design='position_mixture_v1',model=self.model.info,
            settings=c,base_config=self.base_config,estimate=estimate,created=time.time(),
            upstream_commit='d32fed8d4162a4888291c4b3a38b059727c85a41',
            generation_defaults=self.model.model.generation_config.to_dict(),
            effective_sampling=dict(branch_temperature=1,continuation_temperature=c['temperature'],top_p=1,top_k=0),
            versions={p:importlib.metadata.version(p) for p in ['torch','transformers','otrecon','forking-paths']})
        self.save(folder/'manifest.json',metadata)
        plans=list(estimate['passes'])
        if c['dense']:
            plans.append(dict(id='dense',label='Independent reference',positions=estimate['grids']['dense'],samples=c['reference_samples'],seed=0))
        for phase_i,p in enumerate(plans):
            key=p['id']; n=p['samples']; record=self.record(cfg)
            record.update(sampling_design='position_mixture_v1',positions=[])
            record['config'].update(n_samples=n,n0_samples=n)
            started=time.perf_counter(); generated=0
            for t in p['positions']:
                self.check()
                branches=self.branches(cfg,[t])
                selection_seed=[p['seed'],phase_i,t,101]
                picks=allocation(branches,n,selection_seed)
                record['positions'].append(dict(t=t,samples=n,retained_mass=float(sum(b.tok_p for b in branches)),
                    selection_seed=selection_seed,branch_choices=picks,candidates=[dict(tok_id=b.tok_id,tok_p=b.tok_p,is_base=b.is_base) for b in branches]))
                for bi,branch in enumerate(branches):
                    indices=[i for i,v in enumerate(picks) if v==bi]
                    if not indices: continue
                    self.progress(f"{p['label']} · token {t} · {len(indices)} draws from branch {bi+1}",done,total)
                    seed=int(np.random.SeedSequence([p['seed'],phase_i,t,branch.tok_id,202]).generate_state(1)[0])
                    continuations=self.model.draw_branch(branch,len(indices),c['cont_max'],c['temperature'],seed,self.check)
                    if len(continuations)!=len(indices): raise RuntimeError('Model returned an incorrect draw count.')
                    observations=[inspect_continuation(self.model,self.base,branch,cont,c['cont_max'],self.question.get('answers'), rule=(self.base_config or {}).get('outcome_rule')) for cont in continuations]
                    generated+=sum(map(len,continuations))
                    record['branches'].append(dict(t=t,tok_id=branch.tok_id,tok_p=branch.tok_p,is_base=branch.is_base,
                        answers=[o['label'] for o in observations],draw_indices=indices,cont_lens=list(map(len,continuations)),
                        continuation_ids=continuations,observations=observations,seed=seed))
                    done+=len(indices)
                    self.save(folder/f'{key}.json',record)
                    self.progress(f"{p['label']} · token {t} · saved {done} of {total} continuations",done,total)
                    with self.lock:
                        self.job['generated_tokens'] += sum(map(len,continuations))
            records[key]=record
            obs=[o for b in record['branches'] for o in b['observations']]
            phase_costs[key]=dict(continuations=len(obs),continuation_tokens=generated,wall_seconds=time.perf_counter()-started,
                logit_fallback=0,at_continuation_cap=sum(o['stop_reason']=='length' for o in obs),
                unresolved=sum(o.get('label')=='Other' for o in obs))
            if key!='dense':
                self.progress(f"Reconstructing {p['label']}…",done,total)
                curves[key]=reconstruct(record,n,c['tuning'],43_000_000+phase_i)
                self.check()
        reference=None
        if c['dense']:
            pos,draws=position_draws(records['dense'])
            values=od.counts_from_draws(draws,len(self.categories()),0,c['reference_samples'])/c['reference_samples']
            rm=phase_costs['dense']; valid=rm['at_continuation_cap']/rm['continuations']<=.1
            reference=dict(positions=pos,values=values.tolist(),independent=True,valid=valid,
                warning=None if valid else 'Reference exceeds 10% cap hits; comparison metrics withheld.')
            for key in curves:
                if valid and curves[key]['fit_status']=='complete':
                    curves[key]['comparison']=compare(records['dense'],curves[key],c['reference_samples'],44_000_000)
        result=dict(**metadata,base=self.base_metadata(),categories=self.categories(),
            passes=[dict(id=p['id'],label=p['label'],configuration=p,curve=curves[p['id']]) for p in estimate['passes']],
            reference=reference,measured=phase_costs,caveats=[
                'Text matching detects mentions, not semantic correctness. Multiple matched answers, unfinished and unmatched replies count as Other.',
                'Every generated draw is used once. Branches are chosen from their renormalized temperature-1 probabilities.',
                'Omitted branch mass is excluded; these are truncated-mixture outcome estimates.',
                'Fits are withheld above 10% cap hits. This is a diagnostic gate, not a statistical guarantee.',
                'Nominal bands are model-based, may under-cover, and exclude exactly zero and one.',
                'Passes are fitted independently; overlapping checkpoints cost additional independent draws.',
                'No claim of reasoning mechanism, exhaustive fork detection, or matched-accuracy savings.'])
        self.save(folder/'result.json',result)
        with self.lock: self.job['result_id']=run_id

    @staticmethod
    def save(path, data):
        temp=path.with_suffix(".tmp")
        temp.write_text(json.dumps(data,allow_nan=False),encoding="utf-8")
        temp.replace(path)

    def cancel(self, expected_job_id=None):
        with self.lock:
            if expected_job_id is not None and self.job.get('id') != expected_job_id:
                raise ValueError('This is no longer the active job. No other job was stopped.')
            self.stop.set()
            return {"stopping":self.job["status"]=="running"}

    def results(self):
        entries=[]
        for file in sorted(RUNS.glob("*/result.json"),key=lambda p:p.stat().st_mtime,reverse=True)[:100]:
            if file.parent.name.startswith('.'): continue
            try:
                value=json.loads((file.parent/"manifest.json").read_text())
                entries.append(dict(lineage=value.get("lineage"),passes=value.get("settings",{}).get("passes",[]),id=value["id"],model=value["model"]["model_id"],created=value["created"],prompt=value.get("base_config",{}).get("prompt",value.get("base_config",{}).get("question",""))))
            except (OSError, ValueError, KeyError, TypeError):
                # One interrupted or damaged archive must not hide every valid run.
                continue
        return entries

    def import_result(self, value):
        from fork_microscope.evidence_io import import_export
        with self.lock:
            if isinstance(value, dict) and value.get('schema') in ('fork-investigation-bundle-v1','fork-investigation-bundle-v2','fork-investigation-bundle-v3'):
                from fork_microscope.investigation_bundle import import_bundle
                return import_bundle(value, RUNS)
            return import_export(value, RUNS)

    def result(self, run_id, raw=False):
        if len(run_id)!=32 or any(x not in "0123456789abcdef" for x in run_id): raise ValueError("Invalid run ID.")
        folder=RUNS/run_id
        file=folder/"result.json"
        if not file.exists(): raise ValueError("No completed result exists for this run.")
        try:
            result=json.loads(file.read_text())
            if raw:
                result["records"]={p.stem:json.loads(p.read_text()) for p in folder.glob("*.json") if p.stem not in ("result","manifest")}
        except (OSError, ValueError) as exc:
            raise ValueError("This saved run could not be read. Restore it from an evidence export or backup.") from exc
        return result
