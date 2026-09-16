# generated: Codex — manual operations share the durable workflow owner and reservations.
"""Investigation metadata and bounded response search; no independent scheduler."""
import copy
import threading
import time
from fork_microscope.investigation_records import adapt_workflow, validate_context
from fork_microscope.investigation_bundle import digest, identifier


class InvestigationCatalog:
    def create(self, request):
        if not isinstance(request, dict) or set(request) != {'request_id', 'context', 'limits'}:
            raise ValueError('Provide request_id, context and limits.')
        context = validate_context(request['context'])
        limits = request['limits']
        if not isinstance(limits, dict) or set(limits) - {'max_seconds', 'max_samples', 'max_generated_tokens', 'max_attempts'} or not {'max_seconds', 'max_samples', 'max_generated_tokens'} <= set(limits):
            raise ValueError('Provide runtime, continuation sample and generated-token allowances.')
        if any(type(x) is not int or not 1 <= x <= 10**9 for x in limits.values()):
            raise ValueError('Allowances must be positive integers.')
        key = request['request_id']
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise ValueError('Provide a stable request ID.')
        id = digest('manual:' + key)[:32]
        with self.lock:
            if (self.root / (id + '.json')).exists():
                d = self.read(id)
                if d.get('creation_request') != digest(request):
                    raise ValueError('Request ID already belongs to different settings.')
                return d
            d = adapt_workflow(dict(id=id, schema='fork-workflow-v2', record_revision=0, created=time.time(),
                status='draft', config=None, context=context, limits=copy.deepcopy(limits), runs=[], lenses=[], steps=[],
                reservations={'samples': 0, 'generated_tokens': 0, 'attempts': 0}, elapsed_seconds=0,
                cancellation_requested=False, message='No job running', creation_request=digest(request)))
            self.save(d)
            return d

    def update(self, request):
        if not isinstance(request, dict) or set(request) - {'id', 'record_revision', 'context', 'conclusion', 'selected_response_id', 'comparison'}:
            raise ValueError('Invalid investigation update.')
        with self.lock:
            d = adapt_workflow(self.read(request['id']))
            if self.active == d['id']:
                raise ValueError('Wait for the active operation before changing investigation metadata.')
            if 'record_revision' in request and request['record_revision'] != d.get('record_revision', 0):
                raise ValueError('Investigation changed; reload before saving.')
            if 'context' in request: d['context'] = validate_context(request['context'])
            if 'conclusion' in request:
                if not isinstance(request['conclusion'], str) or len(request['conclusion']) > 16000:
                    raise ValueError('Conclusion must be text up to 16,000 characters.')
                d['conclusion'] = request['conclusion']
            if 'selected_response_id' in request:
                if request['selected_response_id'] is not None and request['selected_response_id'] not in d['responses']:
                    raise ValueError('Selected response is not part of this investigation.')
                d['selected_response_id'] = request['selected_response_id']
            if 'comparison' in request:
                c = copy.deepcopy(request['comparison'])
                self.validate_comparison(c, {i: self.service.result(i, raw=True) for i in d['runs']})
                if any(old['id'] == c['id'] and old != c for old in d['comparisons']):
                    raise ValueError('Saved comparison references are immutable; use a new ID.')
                if c not in d['comparisons']: d['comparisons'].append(c)
            if d['schema'] == 'fork-workflow-v1':
                original = self.root / (d['id'] + '.json')
                backup = self.root / (d['id'] + '.v1-backup')
                if not backup.exists(): backup.write_bytes(original.read_bytes())
                d['schema'] = 'fork-workflow-v2'
            d['record_revision'] = d.get('record_revision', 0) + 1
            d.pop('worker_job', None)
            self.save(d)
            return d

    @staticmethod
    def validate_comparison(c, runs):
        if not isinstance(c, dict) or set(c) != {'id', 'run_id', 'pass_id', 'checkpoint', 'draw_indices', 'rationale'}:
            raise ValueError('Comparison requires exact run, pass, checkpoint, two draw indices and rationale.')
        identifier(c['id'])
        if not isinstance(c['rationale'], str) or len(c['rationale']) > 2000:
            raise ValueError('Invalid comparison rationale.')
        pair = c['draw_indices']
        if not isinstance(pair, list) or len(pair) != 2 or len(set(pair)) != 2 or any(type(x) is not int for x in pair):
            raise ValueError('Select two distinct continuation indices.')
        run = runs.get(c['run_id'])
        record = (run or {}).get('records', {}).get(c['pass_id'])
        if record is None: raise ValueError('Comparison source is missing.')
        existing = [i for b in record['branches'] if b['t'] == c['checkpoint'] for i in b.get('draw_indices', [])]
        if any(existing.count(i) != 1 for i in pair): raise ValueError('Comparison continuation is missing or ambiguous.')

    def search(self, request):
        fields = {'id', 'request_id', 'target', 'max_attempts', 'max_tokens', 'seed', 'temperature'}
        if not isinstance(request, dict) or set(request) != fields:
            raise ValueError('Invalid outcome search settings.')
        for key, lo, hi in [('max_attempts', 1, 100), ('max_tokens', 8, 4096), ('seed', 0, 2**31-1)]:
            if type(request[key]) is not int or not lo <= request[key] <= hi: raise ValueError('Invalid ' + key)
        temp = request['temperature']
        if type(temp) not in (int, float) or not 0 <= temp <= 2: raise ValueError('Temperature must be between 0 and 2.')
        if request['target'] is None and request['max_attempts'] != 1:
            raise ValueError('Generate one response uses exactly one attempt.')
        if request['target'] is not None and temp == 0:
            raise ValueError('Outcome search requires stochastic generation; use temperature above zero.')
        return self._manual_start(request, 'search')

    def operation(self, request):
        if not isinstance(request, dict) or set(request) != {'id', 'request_id', 'action', 'payload'} or request['action'] not in ('scan', 'refine', 'lens', 'investigate', 'patch'):
            raise ValueError('Invalid investigation operation.')
        return self._manual_start(request, request['action'])

    def _manual_start(self, request, action):
        key = request['request_id']
        if not isinstance(key, str) or not 1 <= len(key) <= 128: raise ValueError('Provide a stable request ID.')
        with self.lock, self.service.lock:
            d = adapt_workflow(self.read(request['id']))
            if d['schema'] != 'fork-workflow-v2': raise ValueError('Save investigation metadata before running manual operations.')
            for prior in d['operations']:
                if prior['request_id'] == key:
                    if prior['request'] != request: raise ValueError('Request ID already belongs to another operation.')
                    return d  # Never silently repeat interrupted or completed execution.
            if self.active or self.service.job['status'] == 'running': raise ValueError('Worker is busy.')
            limits = d.get('limits') or (d.get('config') or {}).get('limits')
            if not isinstance(limits, dict) or any(type(limits.get(k)) is not int or limits[k] <= 0 for k in ('max_seconds','max_samples','max_generated_tokens')):
                raise ValueError('This historical investigation has no enforceable budget. Create a follow-up investigation with explicit limits.')
            if any(type(d.get('reservations', {}).get(k)) is not int for k in ('samples','generated_tokens')) or type(d.get('elapsed_seconds')) not in (int,float):
                raise ValueError('Historical resource usage is unknown. Create a follow-up investigation with explicit limits.')
            if not self.service.model: raise ValueError('Connect compute and load a model first.')
            if action == 'search':
                validate_context(d['context'])
                if request['target'] is not None and request['target'] not in d['context']['outcome_rule']['answers']:
                    raise ValueError('Choose a target outcome from the current rules.')
            op = dict(request_id=key, request=copy.deepcopy(request), action=action, status='running', response_ids=[])
            d['operations'].append(op)
            if action == 'search': d['searches'].append(dict(id=digest(d['id'] + ':' + key)[:32], request=copy.deepcopy(request), response_ids=[], status='running', completion_reason=None))
            d.update(status='running', cancellation_requested=False, message='Starting ' + action)
            self.cancelled.discard(d['id'])
            self.active = d['id']; self.service.workflow_owner = d['id']; self.save(d)
            threading.Thread(target=self._manual_execute, args=(d, op), daemon=True).start()
            return d

    def _manual_execute(self, d, op):
        from fork_microscope.investigation_workflow import BudgetStop, UserStop
        started = time.monotonic()
        limits = d.get('limits') or d['config']['limits']
        remaining = max(0, limits['max_seconds'] - d['elapsed_seconds'])
        self.deadline = started + remaining; self.service.workflow_deadline = self.deadline
        d['deadline_at'] = time.time() + remaining
        req = op['request']; search = d['searches'][-1] if op['action'] == 'search' else None
        try:
            self.checkpoint(d)
            if search is not None:
                inp = d['context']['input']; rule = d['context']['outcome_rule']
                for attempt in range(req['max_attempts']):
                    self.checkpoint(d)
                    if d['reservations']['generated_tokens'] + req['max_tokens'] > limits['max_generated_tokens']: raise BudgetStop('Next response exceeds the remaining token allowance.')
                    if d['reservations'].get('attempts', 0) >= limits.get('max_attempts', 100): raise BudgetStop('Response attempt allowance reached.')
                    d['reservations']['attempts'] = d['reservations'].get('attempts', 0) + 1
                    payload = dict(prompt=inp['prompt'], mode=inp['mode'], answers=rule['answers'], outcome_rule=rule,
                        max_tokens=req['max_tokens'], seed=(req['seed'] + attempt) % (2**31), temperature=req['temperature'])
                    if 'messages' in inp: payload['messages'] = copy.deepcopy(inp['messages'])
                    job = self.perform(d, 'base', payload, tokens=req['max_tokens'])
                    rid = job['response_id']; response = self.service.response(rid)
                    if rid not in d['responses']: d['responses'].append(rid)
                    op['response_ids'].append(rid); search['response_ids'].append(rid)
                    d['selected_response_id'] = rid
                    self.save(d)
                    if req['target'] is None or response['classification']['label'] == req['target']:
                        search['completion_reason'] = 'generated' if req['target'] is None else 'target_found'; break
                else: search['completion_reason'] = 'target_not_found'
            else:
                action = op['action']; payload = copy.deepcopy(req['payload'])
                samples = tokens = 0
                if action == 'scan':
                    if set(payload) != {'response_id', 'settings'} or payload['response_id'] not in d['responses']:
                        raise ValueError('Select a saved response in this investigation.')
                    response = self.service.response(payload['response_id'])
                    # Replaying scores consumes compute, but generates no new response.
                    self.perform(d, 'select_response', {'response_id': response['id']})
                    estimate = self.service.estimate(payload['settings'])
                    samples, tokens = estimate['total_rollouts'], estimate['max_continuation_tokens']
                    payload = payload['settings']; action = 'run'
                else:
                    if payload.get('source_run_id') not in d['runs']: raise ValueError('Source scan is outside this investigation.')
                    if action == 'refine':
                        plan = self.service.refinement_plan(payload); p = plan['run']['passes'][0]
                        samples = len(p['positions']) * p['samples']; tokens = samples * plan['run']['cont_max']
                    else:
                        plan = getattr(self.service, {'lens':'lens_plan','patch':'patch_plan','investigate':'investigation_plan'}[action])(payload)
                        samples = plan.get('continuations', 0); tokens = plan.get('max_new_tokens', 0)
                        op['replay_positions'] = len(payload.get('positions', [])) or max(0, payload.get('end', -1) - payload.get('start', 0) + 1)
                self.perform(d, action, payload, samples=samples, tokens=tokens)
            d.update(status='idle', message='Operation complete; evidence retained.'); op['status'] = 'complete'
            if search is not None: search['status'] = 'complete'
        except (BudgetStop, UserStop) as exc:
            status = 'cancelled' if isinstance(exc, UserStop) else 'budget_exhausted'
            d.update(status=status, message=str(exc) or 'Cancelled; saved evidence retained.'); op['status'] = status
            if search is not None: search.update(status=status, completion_reason=status)
        except Exception as exc:
            d.update(status='error', message=str(exc)[:1500]); op['status'] = 'error'
            if search is not None: search.update(status='error', completion_reason='error')
        finally:
            d['elapsed_seconds'] += time.monotonic() - started
            d['record_revision'] = d.get('record_revision', 0) + 1
            with self.lock, self.service.lock:
                self.active = None; self.service.workflow_owner = None; self.service.workflow_deadline = None
                # A terminal record must never be observable while its owner is active.
                self.save(d)
