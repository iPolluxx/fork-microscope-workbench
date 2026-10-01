"""Outbound hosted worker. Generation remains the existing WorkflowManager engine.

No model is loaded at boot. A journal fences command retries across process restarts.
Interrupted commands require an explicit new investigation, never automatic replay.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from .evidence_delivery import EvidenceDelivery, upload_drive


class ControlClient:
    def __init__(self, url, *, opener=urlopen):
        p = urlsplit(url)
        if p.scheme != 'https' or not p.hostname or p.username or p.password or p.query or p.fragment:
            raise ValueError('Worker requires a configured HTTPS control-plane URL.')
        self.url, self.open = url.rstrip('/'), opener

    def call(self, action, body):
        request = Request(self.url + '/api/hosted/v1/worker/' + action,
                          data=json.dumps(body, allow_nan=False).encode(), method='POST',
                          headers={'Content-Type': 'application/json', 'User-Agent': 'fork-microscope/0.1'})
        try:
            with self.open(request, timeout=25) as response:
                return json.loads(response.read(2 * 1024 * 1024))
        except HTTPError as exc:
            # Never propagate potentially sensitive provider/HTTP response bodies.
            raise RuntimeError('Control plane rejected request (%s).' % exc.code) from None


class Engine:
    def __init__(self, root):
        # This is instantiated only upon an actual job; boot performs no model load.
        from fork_microscope import live_service
        from fork_microscope.investigation_workflow import WorkflowManager
        root = Path(root)
        live_service.RUNS = root / 'live-runs'
        self.service = live_service.LiveService(root / 'workspace-data')
        self.manager = WorkflowManager(self.service, root / 'workflow-jobs')

    def start(self, job_id, config):
        return self.manager.start({'request_id': job_id, 'config': config})['id']

    def status(self, workflow_id):
        return self.manager.read(workflow_id)

    def cancel(self, workflow_id):
        return self.manager.cancel(workflow_id)

    def export(self, workflow_id):
        return self.manager.export(workflow_id)


class Worker:
    def __init__(self, client, *, session_id, enrollment_token, root, origin, deadline,
                 engine_factory=Engine, clock=time.time):
        self.client, self.session_id, self.enrollment_token = client, session_id, enrollment_token
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock, self.deadline, self.engine_factory = clock, deadline, engine_factory
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.delivery = EvidenceDelivery(self.root / 'exports', origin=origin, deadline=deadline, clock=clock)
        self.path = self.root / 'hosted-journal.json'
        self.journal = json.loads(self.path.read_text()) if self.path.exists() else {'jobs': {}, 'seq': 0}
        if self.journal.get('session_id', session_id) != session_id:
            raise ValueError('Worker data belongs to a different compute session.')
        self.journal['session_id'] = session_id
        self.engine, self.active = None, None
        # Only the previous process knows whether generation actually started.
        for item in self.journal['jobs'].values():
            if item['state'] in ('starting', 'running', 'saving'):
                item['state'] = 'interrupted'
        self.save()

    def save(self):
        with self.lock:
            tmp = self.path.with_suffix('.tmp')
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump(self.journal, f, allow_nan=False)
            tmp.replace(self.path)

    def envelope(self, **extra):
        return dict(session_id=self.session_id, worker_token=self.journal['worker_token'], epoch=self.journal['epoch'], **extra)

    def enroll(self):
        if not self.journal.get('worker_token'):
            response = self.client.call('enroll', {'session_id': self.session_id, 'token': self.enrollment_token})
            self.journal.update(worker_token=response['worker_token'], epoch=response['epoch'])
            self.save()
        self.enrollment_token = ''
        os.environ.pop('FM_ENROLLMENT_TOKEN', None)

    def report(self, job_id, status, **kwargs):
        return self.client.call('report', self.envelope(job_id=job_id, status=status, **kwargs))

    def heartbeat(self):
        with self.lock:
            self.journal['seq'] += 1
            self.save()
            progress = None
            if self.active and self.engine:
                status = self.engine.status(self.journal['jobs'][self.active]['workflow_id'])
                action = (status.get('pending') or {}).get('action')
                phase = {'load':'loading','base':'generating','run':'scanning','refine':'refining','lens':'inspecting'}.get(action,'saving')
                worker = status.get('worker_job') or {}
                progress = dict(phase=phase,completed=max(0,int(worker.get('completed',0))),total=max(0,int(worker.get('total',0))))
            extra = {'progress':progress} if progress else {}
            self.client.call('heartbeat', self.envelope(seq=self.journal['seq'], active_job_id=self.active, **extra))

    def tick(self, *, heartbeat=True):
        self.enroll()
        if heartbeat:
            self.heartbeat()
        # A dispatched command is never redelivered. Close interrupted journals
        # explicitly instead of renewing an apparently active job forever.
        for jid, entry in self.journal['jobs'].items():
            if entry['state'] == 'interrupted' and not entry.get('recovery_reported'):
                artifacts = entry.get('artifacts', [])
                if not artifacts and entry.get('workflow_id'):
                    try:
                        self.engine = self.engine or self.engine_factory(self.root)
                        saved = self.engine.status(entry['workflow_id'])
                        if saved.get('runs') or saved.get('responses'):
                            self.active = jid
                            self.progress(force_status='interrupted')
                            entry['recovery_reported'] = True
                            self.save()
                            continue
                    except Exception:
                        self.active = None
                self.report(jid, 'interrupted', artifacts=artifacts)
                entry['recovery_reported'] = True
                self.save()
        command = self.client.call('poll', self.envelope())
        action = command.get('action')
        if action == 'terminate' or self.clock() >= self.deadline:
            if self.active and self.engine:
                self.engine.cancel(self.journal['jobs'][self.active]['workflow_id'])
            return False
        if action == 'cancel':
            job_id = command.get('job_id') or self.active
            entry = self.journal['jobs'].get(job_id)
            if entry and self.engine and entry.get('workflow_id'):
                self.engine.cancel(entry['workflow_id'])
        if action == 'run':
            job_id = command['job_id']
            entry = self.journal['jobs'].get(job_id)
            if entry is not None and entry['state'] == 'interrupted':
                self.report(job_id, 'interrupted')
            elif entry is None:
                # Persist before executing anything billable.
                self.journal['jobs'][job_id] = {'state': 'starting', 'destination': command.get('destination', 'device')}
                self.save()
                self.engine = self.engine or self.engine_factory(self.root)
                try:
                    config = command['command']['config']
                    workflow = self.engine.start(job_id, config)
                    self.journal['jobs'][job_id].update(state='running', workflow_id=workflow)
                    self.active = job_id
                    self.save()
                except Exception:
                    self.journal['jobs'][job_id]['state'] = 'failed'
                    self.save()
                    self.report(job_id, 'failed')
        if self.active:
            self.progress()
        return True

    def progress(self, force_status=None):
        job_id, entry = self.active, self.journal['jobs'][self.active]
        value = self.engine.status(entry['workflow_id'])
        if value['status'] == 'running' and not force_status:
            return
        terminal = {'complete': 'completed', 'cancelled': 'cancelled', 'interrupted': 'interrupted'}.get(value['status'], 'failed')
        terminal = force_status or terminal
        artifacts = entry.get('artifacts', [])
        if not artifacts and (value.get('runs') or value.get('responses')):
            entry['state'] = 'saving'
            self.save()
            self.report(job_id, 'saving')
            bundle = self.engine.export(entry['workflow_id'])
            record = self.delivery.add(job_id, bundle)
            if entry['destination'] == 'drive':
                grant = self.client.call('artifact-prepare', self.envelope(job_id=job_id, artifact={
                    k: record[k] for k in ('id', 'sha256', 'size_bytes')}))
                uploaded = upload_drive(self.root / 'exports' / (job_id + '.json'), grant['upload_url'], clock=self.clock, deadline=self.deadline)
                artifacts.append(dict(id=record['id'], sha256=record['sha256'], size_bytes=record['size_bytes'], file_ref=grant['file_ref'], destination='drive'))
            else:
                artifacts.append(dict(record, destination='device'))
        entry['artifacts'] = artifacts
        self.save()
        self.report(job_id, terminal, artifacts=artifacts)
        entry['state'] = terminal
        self.save()
        self.active = None

    def run(self):
        self.enroll()
        def renew():
            while not self.stopping.wait(15):
                try:
                    self.heartbeat()
                except Exception:
                    pass
        heartbeat_thread = threading.Thread(target=renew, daemon=True)
        heartbeat_thread.start()
        server = self.delivery.server()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            while self.clock() < self.deadline:
                try:
                    if not self.tick(heartbeat=False):
                        break
                except Exception:
                    # Deadlines remain local even if the control plane is unavailable.
                    # Do not echo HTTP, config, tokens, or model text into provider logs.
                    print('Hosted connection/export interrupted; retrying within the session deadline.', flush=True)
                time.sleep(3)
        finally:
            self.stopping.set()
            if self.active and self.engine:
                self.engine.cancel(self.journal['jobs'][self.active]['workflow_id'])
            server.shutdown()
            server.server_close()


def main():
    deadline = float(os.environ['FM_EXPIRES_AT'])
    if deadline <= time.time():
        raise SystemExit('Session expired before startup.')
    Worker(ControlClient(os.environ['FM_CONTROL_PLANE_URL']), session_id=os.environ['FM_SESSION_ID'],
           enrollment_token=os.environ.pop('FM_ENROLLMENT_TOKEN', ''), root=os.environ.get('FM_WORKER_DATA', '/workspace'),
           origin=os.environ['FM_DASHBOARD_ORIGIN'], deadline=deadline).run()

if __name__ == '__main__':
    main()
