# generated: Codex — investigation catalog and durable-response API routes.
"""Static dashboard and single-owner worker API with opt-in authenticated origins."""
import os
os.environ.setdefault("OTRECON_FORCE_RUPTURES", "1")

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit, parse_qs
from fork_microscope import microscope
from fork_microscope.evidence_io import MAX_IMPORT_BYTES
from fork_microscope.live_service import LiveService, exact
from fork_microscope.worker_connection import WorkerAccess

PUBLIC = Path(__file__).resolve().parents[2] / "public/fork-microscope"
COMPUTE = threading.BoundedSemaphore(2)
FIELDS = {"row", "samples", "stride", "shift", "start", "end", "draw_start"}
LIVE = LiveService()
from fork_microscope.investigation_workflow import WorkflowManager
WORKFLOWS = WorkflowManager(LIVE, Path(__file__).resolve().parents[2] / "workflow-jobs")


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        if "/api/live/status" not in self.path:
            super().log_message(format, *args)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC), **kwargs)

    def json_response(self, status, body):
        data = json.dumps(body, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def access(self):
        return getattr(self.server, 'worker_access', WorkerAccess())

    def authorized(self):
        return self.access().authorize(self.headers.get('Host',''),self.headers.get('Origin'),
            self.headers.get('Authorization'),self.server.server_port)

    def end_headers(self):
        origin=self.headers.get('Origin')
        if self.access().allowed_origin(origin):
            self.send_header('Access-Control-Allow-Origin',origin)
            self.send_header('Vary','Origin')
        self.send_header('X-Content-Type-Options','nosniff')
        super().end_headers()

    def do_OPTIONS(self):
        if not self.access().allowed_origin(self.headers.get('Origin')):
            return self.json_response(403,{'error':'This dashboard origin is not authorized on the worker.'})
        self.send_response(204)
        self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers','Content-Type, Authorization')
        self.send_header('Access-Control-Max-Age','600')
        self.send_header('Access-Control-Allow-Private-Network','true')
        self.end_headers()

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path.startswith('/api/') and not self.authorized():
            return self.json_response(401,{'error':'Connect using this worker’s access token and an explicitly allowed dashboard origin.'})
        if not self.access().remote and self.headers.get('Host','') not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}:
            return self.json_response(403,{'error':'Use the localhost address.'})
        if parsed.path in ("/", "/index.html"): self.path = "/workspace.html"
        if parsed.path.startswith("/api/live/"):
            try:
                if parsed.path == '/api/live/response':
                    q=parse_qs(parsed.query)
                    if set(q) != {'id'} or len(q['id']) != 1: raise ValueError('Provide one response ID.')
                    return self.json_response(200, LIVE.response(q['id'][0]))
                if parsed.path == '/api/live/responses':
                    q=parse_qs(parsed.query)
                    if set(q) - {'investigation_id'}: raise ValueError('Unknown response query.')
                    return self.json_response(200, LIVE.responses(q.get('investigation_id', [None])[0]))
                if parsed.path == '/api/live/workflows' and not parsed.query:
                    return self.json_response(200, WORKFLOWS.list())
                if parsed.path in ('/api/live/workflow', '/api/live/workflow-export', '/api/live/bundle-export'):
                    q = parse_qs(parsed.query)
                    if set(q) != {'id'} or len(q['id']) != 1: raise ValueError('Provide one ID.')
                    if parsed.path.endswith('bundle-export'):
                        from fork_microscope.investigation_bundle import export_family
                        value = export_family(LIVE, q['id'][0])
                    elif parsed.path.endswith('workflow-export'): value = WORKFLOWS.export(q['id'][0])
                    else: value = WORKFLOWS.read(q['id'][0])
                    return self.json_response(200, value)
                if parsed.path == "/api/live/status" and not parsed.query:
                    return self.json_response(200, LIVE.status())
                if parsed.path == '/api/live/investigations' and not parsed.query:
                    return self.json_response(200, LIVE.investigations())
                if parsed.path == '/api/live/lens-options' and not parsed.query:
                    return self.json_response(200, LIVE.lens_options())
                if parsed.path == '/api/live/patch-options' and not parsed.query:
                    return self.json_response(200, LIVE.patch_options())
                if parsed.path == '/api/live/investigation':
                    params = parse_qs(parsed.query)
                    if set(params) != {'id'} or len(params['id']) != 1:
                        raise ValueError('Provide one investigation ID.')
                    return self.json_response(200, LIVE.investigation(params['id'][0]))
                if parsed.path == '/api/live/prompt-sets' and not parsed.query:
                    return self.json_response(200,dict(sets=LIVE.workspace.list('sets')))
                if parsed.path == '/api/live/batches' and not parsed.query:
                    return self.json_response(200,dict(batches=LIVE.workspace.list('batches')))
                if parsed.path == "/api/live/runs" and not parsed.query:
                    return self.json_response(200, LIVE.results())
                if parsed.path in ("/api/live/result", "/api/live/export"):
                    q=parse_qs(parsed.query)
                    if set(q)!={"id"} or len(q["id"])!=1: raise ValueError("Provide one run ID.")
                    return self.json_response(200,LIVE.result(q["id"][0],raw=parsed.path.endswith("export")))
                raise ValueError("Unknown live endpoint.")
            except ValueError as exc:
                return self.json_response(400,{"error":str(exc)})
        if parsed.path.startswith("/api/"):
            if len(parsed.query) > 1024:
                return self.json_response(400, {"error": "Query is too long."})
            try:
                query = parse_qs(parsed.query, keep_blank_values=True)
                if any(len(v) != 1 or not re.fullmatch(r"-?\d{1,6}", v[0]) for v in query.values()):
                    raise ValueError("Use one whole-number value per setting.")
                params = {k: int(v[0]) for k, v in query.items()}
                if parsed.path == "/api/questions" and not params:
                    return self.json_response(200, microscope.catalog())
                if parsed.path == "/api/question" and set(params) == {"row"}:
                    return self.json_response(200, microscope.metadata(params["row"]))
                if parsed.path == "/api/analyze" and set(params) == FIELDS:
                    if not COMPUTE.acquire(timeout=5):
                        return self.json_response(503, {"error": "Analysis is busy. Try again in a moment."})
                    try:
                        result = microscope.analyze(params)
                    finally:
                        COMPUTE.release()
                    return self.json_response(200, result)
                raise ValueError("Unknown endpoint or unexpected settings.")
            except ValueError as exc:
                return self.json_response(400, {"error": str(exc)})
            except Exception:
                return self.json_response(500, {"error": "Reconstruction failed for these settings. Try a wider region."})
        if parsed.path not in ("/", "/index.html", "/released-data.html", "/app.js", "/math.mjs", "/passes.mjs", "/graph-evidence.mjs", "/observatory.html", "/observatory.mjs", "/refinement-panel.mjs", "/refinement-panel.css", "/investigation-panel.mjs", "/investigation-panel.css", "/lens-panel.mjs", "/lens-panel.css", "/patching-panel.mjs", "/patching-panel.css", "/journey.css", "/walkthrough.mjs", "/evidence-import.mjs", "/observatory.css", "/observatory-base.css", "/styles.css", "/plotly.min.js", "/live.html", "/live.js", "/compute-readiness.mjs", "/live.css", "/workspace.html", "/workspace.mjs", "/workspace.css", "/compare.html", "/compare.mjs", "/compare.css", "/worker-connection.js", "/method-credit.js", "/job-progress.mjs", "/response-review.mjs", "/download.mjs", "/workflow-panel.mjs", "/guide.html", "/guide.css", "/guide.mjs", "/app-navigation.css", "/request-retry.mjs", "/scoped-operation.mjs", "/classification.mjs", "/offline-evidence.mjs", "/demo-attendance.json", "/selection.mjs", "/investigation-shell.mjs", "/investigation-shell.css", "/investigation-workbench.mjs"):
            return self.send_error(404)
        super().do_GET()

    def do_POST(self):
        if self.path == '/api/pair':
            pairing = getattr(self.server, 'pairing', None)
            if not pairing: return self.json_response(404, {'error':'Start this worker with machine start to enable pairing.'})
            try:
                if self.headers.get('Content-Type','').split(';')[0] != 'application/json': raise ValueError('JSON required.')
                size = int(self.headers.get('Content-Length','0'))
                if not 0 < size <= 1024: raise ValueError('Invalid pairing request size.')
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload,dict) or set(payload) != {'secret'}: raise ValueError('Provide one pairing secret.')
                return self.json_response(200, pairing.exchange(payload['secret'], self.headers.get('Origin'), self.access().origins))
            except (ValueError, OSError):
                return self.json_response(403, {'error':'Pairing code expired, already used, invalid, or not allowed for this website. Run machine pair for a new code.'})
        if not self.authorized():
            return self.json_response(401,{'error':'Worker access is not authorized. Check the token and allowed dashboard origin.'})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.json_response(415,{"error":"JSON requests are required."})
        try:
            route=urlsplit(self.path)
            size=int(self.headers.get("Content-Length", "0"))
            limit=MAX_IMPORT_BYTES if route.path == "/api/live/import" else (2*1024*1024 if route.path == "/api/live/prompt-set" else 65536)
            if not 0<size<=limit: raise ValueError("Evidence imports must be under 64 MB." if limit==MAX_IMPORT_BYTES else "Invalid request size.")
            payload=json.loads(self.rfile.read(size))
            if route.query: raise ValueError("Unexpected query string.")
            if route.path == '/api/live/classifier-preview':
                from fork_microscope.investigation_records import classify
                exact(payload, 'rule text complete')
                return self.json_response(200, classify(payload['rule'], payload['text'], payload['complete']))
            if route.path in ('/api/live/workflow-create','/api/live/workflow-update','/api/live/workflow-search','/api/live/workflow-operation'):
                action=route.path.rsplit('-',1)[-1]
                return self.json_response(202 if action in ('search','operation') else 200, getattr(WORKFLOWS, action)(payload))
            if route.path == '/api/live/workflow-start':
                return self.json_response(202, WORKFLOWS.start(payload))
            if route.path in ('/api/live/workflow-cancel', '/api/live/workflow-resume'):
                exact(payload, 'id')
                method = WORKFLOWS.cancel if route.path.endswith('cancel') else WORKFLOWS.resume
                return self.json_response(200, method(payload['id']))
            if route.path == '/api/live/model-preflight':
                from fork_microscope.model_preflight import preflight_model
                return self.json_response(200,preflight_model(payload,LIVE.runtime_metadata()))
            if route.path == '/api/live/prompt-set':
                return self.json_response(200,dict(set=LIVE.workspace.save_set(payload)))
            if route.path == '/api/live/prompt-set-delete':
                exact(payload,'id')
                return self.json_response(200,LIVE.workspace.delete_set(payload['id']))
            if route.path == '/api/live/compare':
                from fork_microscope.run_comparison import compare_runs
                exact(payload,'left_run_id left_pass_id right_run_id right_pass_id')
                if any(not isinstance(value,str) for value in payload.values()): raise ValueError('Select run and pass IDs.')
                left=LIVE.result(payload['left_run_id'],raw=True);right=LIVE.result(payload['right_run_id'],raw=True)
                return self.json_response(200,compare_runs(left,right,payload['left_pass_id'],payload['right_pass_id']))
            if route.path == "/api/live/import":
                return self.json_response(200,LIVE.import_result(payload))
            if route.path == "/api/live/refinement-plan":
                return self.json_response(200,LIVE.refinement_plan(payload))
            if route.path == '/api/live/investigation-plan':
                return self.json_response(200, LIVE.investigation_plan(payload))
            if route.path == '/api/live/lens-plan':
                return self.json_response(200, LIVE.lens_plan(payload))
            if route.path == '/api/live/patch-plan':
                return self.json_response(200, LIVE.patch_plan(payload))
            if route.path == "/api/live/estimate":
                with LIVE.lock:
                    if LIVE.job["status"]=="running": raise ValueError("Wait for the current job before estimating.")
                    estimate = LIVE.estimate(payload)
                return self.json_response(200,estimate)
            if route.path == "/api/live/stop":
                if payload == {}: return self.json_response(200,LIVE.cancel())
                if (type(payload) is not dict or set(payload) != {'job_id'}
                        or not isinstance(payload['job_id'], str)):
                    raise ValueError("Provide the active job ID or an empty object.")
                return self.json_response(200,LIVE.cancel(payload['job_id']))
            if route.path in ("/api/live/load","/api/live/base","/api/live/run","/api/live/unload","/api/live/refine","/api/live/batch","/api/live/investigate","/api/live/lens","/api/live/patch"):
                return self.json_response(202,LIVE.start(route.path.rsplit("/",1)[-1],payload))
            raise ValueError("Unknown action.")
        except (ValueError,TypeError) as exc:
            return self.json_response(400,{"error":str(exc)})
        except Exception:
            return self.json_response(500,{"error":"The worker could not complete this request. Check worker storage and logs, then retry."})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument('--host',default='127.0.0.1',help='Bind address; network access requires a worker token.')
    parser.add_argument('--allow-origin',action='append',default=[],help='Explicit dashboard origin allowed to access this worker.')
    args = parser.parse_args()
    try:
        access=WorkerAccess(args.host,os.environ.get('FORK_WORKER_TOKEN',''),args.allow_origin)
    except ValueError as exc:parser.error(str(exc))
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.worker_access=access
    print(f"Fork microscope: http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
