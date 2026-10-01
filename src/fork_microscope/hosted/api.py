"""HTTP routing. Authentication is mandatory and integrations are injected."""
import hmac
from .service import HostedError

def create_app(service, authenticator, *, worker_enabled=False, controller_enabled=False, controller_token=None, allowed_origins=None):
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool
    from starlette.middleware.cors import CORSMiddleware
    app=FastAPI(title='Fork Microscope hosted')
    if allowed_origins:
        app.add_middleware(CORSMiddleware,allow_origins=list(allowed_origins),allow_credentials=False,allow_methods=['GET','POST','PUT','DELETE'],allow_headers=['Authorization','Idempotency-Key','Content-Type'])
    @app.exception_handler(HostedError)
    async def error(request, exc): return JSONResponse(status_code=exc.status,content={'detail':exc.message})
    @app.exception_handler(ValueError)
    async def invalid(request, exc): return JSONResponse(status_code=422,content={'detail':'Invalid request values'})
    @app.exception_handler(Exception)
    async def unavailable(request, exc): return JSONResponse(status_code=503,content={'detail':'Hosted integration unavailable'})
    def identity(request):
        auth=request.headers.get('authorization','')
        if not auth.startswith('Bearer '): raise HostedError(401,'Bearer authentication required')
        try: user=authenticator.verify(auth[7:])
        except Exception: raise HostedError(401,'Authentication failed')
        return user['uid']
    async def body(request):
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>65536: raise HostedError(413,'Request too large')
        import json
        try: value=json.loads(raw)
        except Exception: raise HostedError(422,'Invalid JSON')
        if not isinstance(value,dict): raise HostedError(422,'JSON object required')
        return value
    def endpoint(operation, mutation=False):
        async def handle(request: Request):
            uid=identity(request)
            return await run_in_threadpool(service.public,uid,operation,await body(request) if mutation and request.method not in {'DELETE'} else {},request.path_params.get('resource_id'),request.headers.get('idempotency-key'))
        return handle
    base='/api/hosted/v1'
    for path,method,operation,mutation in [('/me','GET','me',False),('/models','GET','models',False),('/connections/runpod','PUT','runpod_put',True),('/connections/runpod','DELETE','runpod_delete',True),('/connections/drive','GET','drive',False),('/quotes','POST','quote',True),('/sessions','POST','start',True),('/sessions','GET','sessions',False),('/sessions/{resource_id}','GET','session',False),('/sessions/{resource_id}/terminate','POST','terminate',True),('/jobs','POST','job',True),('/jobs','GET','jobs',False),('/jobs/{resource_id}','GET','get_job',False),('/jobs/{resource_id}/cancel','POST','cancel',True),('/artifacts','GET','artifacts',False),('/artifacts/{resource_id}/download','GET','download',False)]:
        app.add_api_route(base+path,endpoint(operation,mutation),methods=[method])
    if worker_enabled:
        @app.post(base+'/worker/artifact-prepare')
        async def prepare(request: Request): return await run_in_threadpool(service.prepare_artifact,await body(request))
        for name in ['enroll','poll','heartbeat','report']:
            def handler(name):
                async def handle(request: Request): return await run_in_threadpool(getattr(service,name),await body(request))
                return handle
            app.add_api_route(base+'/worker/'+name,handler(name),methods=['POST'])
    if controller_enabled:
        if not controller_token: raise ValueError('Controller token required')
        @app.post('/internal/reconcile')
        async def reconcile(request: Request):
            if not hmac.compare_digest(request.headers.get('authorization',''),'Bearer '+controller_token): raise HostedError(401,'Controller authentication required')
            return await run_in_threadpool(service.reconcile)
    return app
