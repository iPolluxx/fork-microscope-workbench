"""Explicit GCP-only pilot factories. No cloud calls on import; no local issuer.

Deployment requires a verified exact Firebase UID and actual ChatGPT client /
callback. The operator config is not tool input. Existing service accounts and
controller invoker grants are sufficient; no keys or new permissions are created.
"""
from contextlib import asynccontextmanager
import asyncio
import json
import os
import re
import time
from urllib.parse import urlsplit

from fork_microscope.hosted import bootstrap as base
from fork_microscope.hosted.auth import AuthenticationError
from fork_microscope.hosted.credentials import adc_token_provider, GoogleSecretVault, UrllibTransport
from hosted_pilot import PilotAccountLink, create_owner_resolver
from fork_microscope_hosted_mcp import artifact_reader
from pilot_budget import BudgetedService, PilotBudget, COLLECTION
from pilot_evidence import PilotEvidenceLibrary, firestore_chunk_deleter
from tools.hosted import ManagedCompute, create_hosted_mcp


class MCPRequestLimits:
    """No idle SSE connections or unbounded MCP bodies, including direct API access."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        from starlette.responses import JSONResponse
        if scope['type'] != 'http' or scope.get('path') != '/mcp':
            return await self.app(scope,receive,send)
        if scope.get('method') == 'GET':
            return await JSONResponse({'detail':'Use stateless POST requests'},status_code=405)(scope,receive,send)
        if scope.get('method') != 'POST':
            return await self.app(scope,receive,send)
        raw=bytearray()
        async def read():
            while True:
                event=await receive()
                if event['type'] != 'http.request':
                    raise ValueError()
                raw.extend(event.get('body',b''))
                if len(raw)>65536:
                    raise ValueError()
                if not event.get('more_body',False):
                    return
        try:
            await asyncio.wait_for(read(),timeout=10)
        except (ValueError,TimeoutError):
            return await JSONResponse({'detail':'MCP request body rejected'},status_code=413)(scope,receive,send)
        delivered=False
        async def bounded_receive():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered=True
            return {'type':'http.request','body':bytes(raw),'more_body':False}
        return await self.app(scope,bounded_receive,send)


class OneAccountAuthenticator:
    def __init__(self, authenticator, uid):
        self.authenticator, self.uid = authenticator, uid

    def verify(self, bearer):
        identity = self.authenticator.verify(bearer)
        if identity.get('uid') != self.uid:
            raise AuthenticationError('pilot account mismatch')
        return identity


def mount_import_api(app, *, library, authenticator, issuer):
    """First-party file bytes only; authentication precedes bounded body consumption."""
    from fastapi import Request
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse
    from fork_microscope.hosted.service import HostedError
    quiet = {'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff'}
    def owner(request):
        if request.headers.get('origin') not in (None, issuer):
            raise HostedError(403, 'Open the import form on your Fork Microscope website')
        auth = request.headers.get('authorization', '')
        if not auth.startswith('Bearer '):
            raise HostedError(401, 'Sign in to your invited workspace before importing')
        try:
            uid = authenticator.verify(auth[7:])['uid']
            library._owner(uid)
            return uid
        except Exception:
            raise HostedError(401, 'Sign in with the approved Fork Microscope account') from None
    async def imported(request: Request):
        try:
            uid = await run_in_threadpool(owner, request)
            if (request.headers.get('content-type','').split(';')[0].strip().lower() != 'application/json'
                    or request.headers.get('content-encoding','identity') != 'identity'):
                raise HostedError(415, 'Upload the original JSON file without compression')
            checksum = request.headers.get('x-fork-evidence-sha256', '')
            request_id = request.headers.get('idempotency-key', '')
            synthetic = request.headers.get('x-fork-evidence-synthetic', 'false')
            if not re.fullmatch(r'[0-9a-f]{64}', checksum) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',request_id):
                raise HostedError(422, 'A file SHA-256 and stable import request ID are required')
            if synthetic not in ('true','false'):
                raise HostedError(422, 'Synthetic evidence label must be true or false')
            raw = bytearray()
            async def read():
                async for chunk in request.stream():
                    if len(raw)+len(chunk) > library.max_artifact:
                        raise HostedError(413, 'Choose a native investigation JSON file of at most 16 MiB')
                    raw.extend(chunk)
            try:
                await asyncio.wait_for(read(), timeout=10)
            except TimeoutError:
                raise HostedError(408, 'File transfer timed out; retry the same file and request') from None
            data = bytes(raw)
            del raw
            saved = await run_in_threadpool(library.import_bytes, uid, data, checksum, request_id,
                                           synthetic=synthetic == 'true')
            return JSONResponse({'saved_evidence':saved},headers=quiet)
        except HostedError as error:
            return JSONResponse({'detail':error.message},status_code=error.status,headers=quiet)
        except Exception:
            return JSONResponse({'detail':'Import could not be confirmed; retry the same file and request'},
                                status_code=503,headers=quiet)
    async def listing(request: Request):
        try:
            uid = await run_in_threadpool(owner, request)
            try:
                limit, offset = int(request.query_params.get('limit','10')), int(request.query_params.get('offset','0'))
            except ValueError:
                raise HostedError(422, 'Use a valid saved-evidence page') from None
            def read():
                with app.state.service.pilot_operation('recovery'):
                    return library.list_investigations(uid, limit=limit, offset=offset)
            return JSONResponse(await run_in_threadpool(read),headers=quiet)
        except HostedError as error:
            return JSONResponse({'detail':error.message},status_code=error.status,headers=quiet)
        except ValueError:
            return JSONResponse({'detail':'Use a valid saved-evidence page'},status_code=422,headers=quiet)
    app.add_api_route('/api/hosted/v1/evidence/import', imported, methods=['POST'])
    app.add_api_route('/api/hosted/v1/evidence', listing, methods=['GET'])
    return app


def _required_account(env):
    uid = env.get('FM_PILOT_UID', '')
    if not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', uid):
        raise base.ConfigError('Verified FM_PILOT_UID is required')
    # Public example binding. Review and set this exact project for a private deployment.
    if env.get('FM_GCP_PROJECT') != 'fork-microscope-pilot' or env.get('FM_FIRESTORE_DATABASE') != 'fork-hosted':
        raise base.ConfigError('Pilot must use its approved existing project and database')
    if env.get('FM_FIRESTORE_NAMESPACE', 'hosted_v1') != 'hosted_v1' or env.get('FM_DRIVE_ENABLED') != 'false':
        raise base.ConfigError('Pilot must use its approved namespace and device-only mode')
    return uid


def required_identity(env):
    if (env.get('FM_PILOT_ACTIVE') != 'true' or env.get('FM_PILOT_CLIENT_VERIFIED') != 'true'
            or env.get('FM_PILOT_DISCOVERY', 'false') != 'false'):
        raise base.ConfigError('Active pilot requires verified client configuration and discovery disabled')
    return _required_account(env)


def required_discovery_identity(env):
    if (env.get('FM_PILOT_DISCOVERY') != 'true' or env.get('FM_PILOT_ACTIVE') != 'false'
            or env.get('FM_PILOT_CLIENT_VERIFIED') != 'false'):
        raise base.ConfigError('Discovery requires FM_PILOT_DISCOVERY=true, FM_PILOT_ACTIVE=false and FM_PILOT_CLIENT_VERIFIED=false')
    return _required_account(env)


class DiscoveryService(BudgetedService):
    """Preserve existing recovery routes while denying new work unconditionally.

    An old enabled budget document cannot enable compute or credential mutation.
    Existing worker capability routes remain in the original public assembly;
    this wrapper cannot provision or queue a new session/job for those workers.
    """
    def public(self, uid, operation, *args, **kwargs):
        self.budget.check_owner(uid)
        if operation not in self.RECOVERY:
            from fork_microscope.hosted.service import HostedError
            raise HostedError(503, 'New work is paused while the private connection is being verified')
        return super().public(uid,operation,*args,**kwargs)


def mount_discovery(app, *, issuer):
    """Static public discovery only: no provider, token verification, state or tools."""
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    parsed = urlsplit(issuer)
    if (parsed.scheme != 'https' or not re.fullmatch(r'[a-z0-9.-]+',parsed.netloc or '')
            or parsed.path or parsed.query or parsed.fragment):
        raise base.ConfigError('Discovery requires the exact HTTPS dashboard origin')
    quiet = {'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'}
    async def metadata(request):
        return JSONResponse({'issuer':issuer,'authorization_endpoint':issuer+'/authorize',
            'token_endpoint':issuer+'/token','revocation_endpoint':issuer+'/revoke',
            'scopes_supported':['fork:manage'],'response_types_supported':['code'],
            'grant_types_supported':['authorization_code','refresh_token'],
            'token_endpoint_auth_methods_supported':['none'],
            'revocation_endpoint_auth_methods_supported':['none'],
            'code_challenge_methods_supported':['S256'],'client_id_metadata_document_supported':True,
            'authorization_response_iss_parameter_supported':True},headers=quiet)
    async def resource(request):
        return JSONResponse({'resource':issuer+'/mcp','authorization_servers':[issuer],
            'scopes_supported':['fork:manage'],'bearer_methods_supported':['header']},headers=quiet)
    async def challenge(request):
        return JSONResponse({'error':'unauthorized'},status_code=401,headers=dict(quiet,
            **{'WWW-Authenticate':'Bearer resource_metadata="'+issuer+'/.well-known/oauth-protected-resource/mcp", scope="fork:manage"'}))
    async def paused(request):
        return JSONResponse({'error':'temporarily_unavailable'},status_code=503,headers=dict(quiet,**{'Retry-After':'60'}))
    routes = [Route('/mcp',challenge,methods=['GET','POST']),
        Route('/.well-known/oauth-authorization-server',metadata,methods=['GET']),
        Route('/.well-known/oauth-protected-resource',resource,methods=['GET']),
        Route('/.well-known/oauth-protected-resource/mcp',resource,methods=['GET'])]
    routes.extend(Route(path,paused,methods=['GET','POST'])
                  for path in ('/authorize','/token','/revoke','/oauth/consent'))
    app.router.routes.extend(routes)
    return app


def mount_managed_mcp(app, *, provider, library, pilot_uid, budget, issuer, api_host):
    if not re.fullmatch(r'[a-z0-9.-]+', api_host or ''):
        raise base.ConfigError('Exact FM_PILOT_API_HOST is required')
    link = PilotAccountLink(issuer=issuer, subject=pilot_uid, hosted_uid=pilot_uid, resource=issuer+'/mcp')
    resolver = create_owner_resolver(link, access_check=provider.access_check)
    def owner(token):
        # Classify admissions at the operation/evidence boundary. A blanket
        # new-request admission here would disable recovery tools at stop.
        return resolver(token)
    facade = ManagedCompute(app.state.service, setup_url=issuer+'/hosted.html', evidence_library=library)
    mcp = create_hosted_mcp(facade, auth=provider, owner_resolver=owner)
    http = mcp.http_app(path='/mcp', transport='http', stateless_http=True, json_response=True,
                        allowed_hosts=[api_host, urlsplit(issuer).hostname], allowed_origins=[issuer])
    previous = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with previous(application), http.lifespan(application):
            yield
    app.router.lifespan_context = lifespan
    app.mount('/', MCPRequestLimits(http))
    app.state.pilot_library, app.state.pilot_mcp, app.state.pilot_budget = library, mcp, budget
    return app


def _assemble_pilot_public(env, uid, *, discovery=False):
    config = base.PublicConfig.from_env(env)
    if config.firebase_project != config.project:
        raise base.ConfigError('Pilot Firebase authentication must use the approved existing project')
    store = base._firestore(config)
    budget = PilotBudget(store, pilot_uid=uid, clock=time.time)
    revocation = base.IdentityToolkitRevocation(config.firebase_project, adc_token_provider())
    firebase = base.FirebaseAuthenticator(config.firebase_project, base.StaticInvites(config.invited_emails), revocation)
    authenticator = OneAccountAuthenticator(firebase, uid)
    host = urlsplit(config.controller_url).hostname
    controller = base.ControllerClient(config.controller_url, base._google_id_token_provider(config.oidc_audience),
                                       UrllibTransport({host}, max_response_bytes=1024*1024))
    deps = base.PublicDeps(store, authenticator, GoogleSecretVault(config.project), controller,
                           service_wrapper=lambda service:(DiscoveryService if discovery else BudgetedService)(service,budget))
    app = base.assemble_public_app(config, deps)
    return app, config, store, budget, revocation, authenticator, controller


def create_cloud_discovery_app(environ=None):
    """Explicit first-stage entrypoint; no client/callback assumptions activate auth.

    Use the same reviewed image and existing public service identity. The private
    controller and existing worker routes are unchanged. Static discovery calls
    do not touch Firestore, Firebase, provider accounts, credentials or budgets.
    """
    env = os.environ if environ is None else environ
    uid = required_discovery_identity(env)
    app, config, *_ = _assemble_pilot_public(env,uid,discovery=True)
    return mount_discovery(app,issuer=config.dashboard_origin)


def create_cloud_public_app(environ=None):
    env = os.environ if environ is None else environ
    uid = required_identity(env)
    app, config, store, budget, revocation, authenticator, controller = _assemble_pilot_public(env,uid)
    from pilot_oauth import PilotOAuthConfig, PilotOAuthProvider, verified_firebase_identity
    oauth_config = PilotOAuthConfig(issuer=config.dashboard_origin,resource=config.dashboard_origin+'/mcp',
        pilot_uid=uid,client_id=env.get('FM_PILOT_CIMD_URL',''),redirect_uri=env.get('FM_PILOT_CALLBACK',''))
    def account_access(account_uid, issued_at):
        if account_uid != uid:
            return False
        revocation.check(account_uid, issued_at)
        return True
    try:
        firebase_config = json.loads(env.get('FM_PILOT_FIREBASE_CONFIG','{}'))
        if firebase_config.get('projectId') != config.firebase_project:
            raise ValueError()
    except (ValueError, AttributeError):
        raise base.ConfigError('Exact public FM_PILOT_FIREBASE_CONFIG is required') from None
    provider = PilotOAuthProvider(oauth_config, store=store,
        firebase_verify=lambda token:verified_firebase_identity(authenticator,token),
        check_account_access=account_access, admission=lambda:budget.admit(kind='oauth'),
        recovery_admission=lambda:budget.admit(kind='recovery'),
        firebase_config=firebase_config)
    library = PilotEvidenceLibrary(app.state.service,store=store,pilot_uid=uid,
        artifact_reader=artifact_reader(controller),delete_chunks=firestore_chunk_deleter(store.client),
        clock=time.time,admission=budget.admit)
    mount_import_api(app,library=library,authenticator=authenticator,issuer=config.dashboard_origin)
    return mount_managed_mcp(app,provider=provider,library=library,pilot_uid=uid,budget=budget,
                             issuer=config.dashboard_origin,api_host=env.get('FM_PILOT_API_HOST',''))


def create_cloud_controller_app(environ=None):
    env = os.environ if environ is None else environ
    uid = required_identity(env)
    config = base.ControllerConfig.from_env(env)
    store = base._firestore(config)
    holder = {}
    from pilot_oauth import firestore_oauth_cleanup
    oauth_cleanup = firestore_oauth_cleanup(store,pilot_uid=uid,clock=time.time)
    def retention():
        now = time.time()
        def claim(tx):
            state = tx.get(COLLECTION,'evidence_retention_schedule') or {'next_at':0}
            if state['next_at'] > now:
                return False
            tx.set(COLLECTION,'evidence_retention_schedule',{'next_at':now+3600})
            return True
        if not store.transaction(claim):
            return {'status':'not_due'}
        result = {}
        for name, operation in (
                ('evidence',lambda:holder['library'].cleanup_expired(limit=4)),
                ('oauth',oauth_cleanup)):
            try:
                result[name] = operation()
            except Exception:
                # Either maintenance task may retry next hour independently;
                # neither may suppress the provider reconcile that ran first.
                result[name] = {'status':'retry_required'}
        return result
    deps = base.ControllerDeps(store,GoogleSecretVault(config.project),base.OidcVerifier(config.oidc_audience),
                                after_reconcile=retention)
    app = base.assemble_controller_app(config,deps)
    holder['library'] = PilotEvidenceLibrary(app.state.service,store=store,pilot_uid=uid,
        artifact_reader=lambda *_: (_ for _ in ()).throw(RuntimeError('Controller cannot fetch pilot evidence')),
        delete_chunks=firestore_chunk_deleter(store.client),clock=time.time)
    return app
