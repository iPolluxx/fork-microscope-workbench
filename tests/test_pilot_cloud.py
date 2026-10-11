"""Cloud factory activation gates and no-network assembly checks."""
import pytest
from pilot_cloud import OneAccountAuthenticator, required_identity, required_discovery_identity
from fork_microscope.hosted.auth import AuthenticationError
from fork_microscope.hosted.bootstrap import ConfigError

def environment():
    return dict(FM_PILOT_ACTIVE='true',FM_PILOT_CLIENT_VERIFIED='true',FM_PILOT_UID='alice',
                FM_GCP_PROJECT='fork-microscope-pilot',FM_FIRESTORE_DATABASE='fork-hosted',
                FM_FIRESTORE_NAMESPACE='hosted_v1',FM_DRIVE_ENABLED='false')

@pytest.mark.parametrize('field,value',[
 ('FM_PILOT_ACTIVE','false'),('FM_PILOT_CLIENT_VERIFIED','false'),('FM_PILOT_UID',''),
 ('FM_PILOT_DISCOVERY','true'),
 ('FM_PILOT_UID','a/b'),('FM_GCP_PROJECT','other-project'),('FM_FIRESTORE_DATABASE','(default)'),
 ('FM_FIRESTORE_NAMESPACE','other'),('FM_DRIVE_ENABLED','true')])
def test_activation_requires_exact_config(field,value):
    env=environment();env[field]=value
    with pytest.raises(ConfigError):required_identity(env)

def test_verified_config_and_exact_authenticator():
    assert required_identity(environment())=='alice'
    class Auth:
        def verify(self,token):return {'uid':token,'email':'same@example.test'}
    auth=OneAccountAuthenticator(Auth(),'alice')
    assert auth.verify('alice')['uid']=='alice'
    with pytest.raises(AuthenticationError):auth.verify('bob')


def test_hourly_maintenance_is_bounded_and_cleanup_tasks_fail_independently(monkeypatch):
    from types import SimpleNamespace
    import pilot_cloud as cloud
    import pilot_oauth
    from fork_microscope.hosted.store import MemoryStore
    store=MemoryStore(); now=[100.]; calls=[]; captured={}
    config=SimpleNamespace(project='fork-microscope-pilot',oidc_audience='https://controller.example.test')
    monkeypatch.setattr(cloud.base.ControllerConfig,'from_env',lambda _:config)
    monkeypatch.setattr(cloud.base,'_firestore',lambda _:store)
    monkeypatch.setattr(cloud,'GoogleSecretVault',lambda _:object())
    monkeypatch.setattr(cloud.base,'OidcVerifier',lambda _:object())
    monkeypatch.setattr(cloud.time,'time',lambda:now[0])
    def assemble(config,deps):
        captured['deps']=deps
        return SimpleNamespace(state=SimpleNamespace(service=object()))
    monkeypatch.setattr(cloud.base,'assemble_controller_app',assemble)
    def oauth():calls.append('oauth');return {'removed_oauth_rows':0}
    monkeypatch.setattr(pilot_oauth,'firestore_oauth_cleanup',lambda *a,**k:oauth)
    monkeypatch.setattr(cloud,'firestore_chunk_deleter',lambda _:None)
    store.client=object()
    def evidence(*,limit):
        assert limit==4;calls.append('evidence');raise RuntimeError('failure')
    monkeypatch.setattr(cloud,'PilotEvidenceLibrary',lambda *a,**k:SimpleNamespace(cleanup_expired=evidence))
    cloud.create_cloud_controller_app(environment())
    maintenance=captured['deps'].after_reconcile
    result=maintenance()
    assert calls==['evidence','oauth'] and result['evidence']=={'status':'retry_required'}
    assert result['oauth']=={'removed_oauth_rows':0}
    assert maintenance()=={'status':'not_due'} and len(calls)==2
    now[0]+=3600
    maintenance()
    assert calls==['evidence','oauth','evidence','oauth']


def test_mounted_mcp_oauth_isolation_stateless_and_body_limits():
    import asyncio
    from datetime import datetime, timezone
    import httpx
    from fastapi import FastAPI
    from test_pilot_oauth import Fixture, ISSUER, UID, grant
    from pilot_budget import PilotBudget, BudgetedService, COLLECTION
    from pilot_cloud import mount_managed_mcp
    from fork_microscope.hosted.service import HostedService
    fixture=Fixture()
    budget=PilotBudget(fixture.store,pilot_uid=UID,clock=lambda:fixture.now)
    month=datetime.fromtimestamp(fixture.now,timezone.utc).strftime('%Y-%m')
    fixture.store.transaction(lambda tx:tx.set(COLLECTION,'pilot_budget_control',dict(
        enabled=True,observed_at=fixture.now,estimated_total_usd=1.2,month=month)))
    class Library:
        def profile_for(self,*_):raise AssertionError('No bundle requested')
        def list_investigations(self,uid,offset=0,limit=20):
            assert uid==UID
            return {'investigations':[],'total_candidates':0}
    app=FastAPI()
    app.state.service=BudgetedService(HostedService(fixture.store,clock=lambda:fixture.now),budget)
    mount_managed_mcp(app,provider=fixture.provider,library=Library(),pilot_uid=UID,
                     budget=budget,issuer=ISSUER,api_host='pilot.example.test')
    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ISSUER) as client:
                headers={'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-06-18'}
                call={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{
                    'name':'fork_microscope_list_investigations','arguments':{}}}
                assert (await client.post('/mcp',json=call,headers=headers)).status_code==401
                tokens=await grant(client)
                headers['Authorization']='Bearer '+tokens['access_token']
                response=await client.post('/mcp',json=call,headers=headers)
                assert response.status_code==200,response.text
                assert response.headers['content-type'].startswith('application/json')
                assert response.json()['result'].get('isError') is not True,response.text
                assert (await client.get('/mcp',headers=headers)).status_code==405
                assert (await client.post('/mcp',content=b'x'*65537,headers=headers)).status_code==413
                fixture.store.transaction(lambda tx:tx.set(COLLECTION,'pilot_budget_control',{'enabled':False}))
                response=await client.post('/mcp',json=call,headers=headers)
                assert response.status_code==200 and response.json()['result'].get('isError') is not True
                fixture.allowed=False
                assert (await client.post('/mcp',json=call,headers=headers)).status_code==401
    asyncio.run(run())


def discovery_environment():
    return dict(environment(),FM_PILOT_DISCOVERY='true',FM_PILOT_ACTIVE='false',
        FM_PILOT_CLIENT_VERIFIED='false',FM_HOSTED_ENV='production',
        FM_FIREBASE_PROJECT_ID='fork-microscope-pilot',FM_INVITED_EMAILS='fixture@example.test',
        FM_DASHBOARD_ORIGIN='https://pilot.example.test',FM_PUBLIC_URL='https://api.example.test',
        FM_CONTROLLER_URL='https://controller.example.test',
        FM_CONTROLLER_OIDC_AUDIENCE='https://controller.example.test')


@pytest.mark.parametrize('field,value',[
 ('FM_PILOT_DISCOVERY','false'),('FM_PILOT_DISCOVERY',''),('FM_PILOT_ACTIVE','true'),
 ('FM_PILOT_ACTIVE',''),('FM_PILOT_CLIENT_VERIFIED','true'),('FM_PILOT_CLIENT_VERIFIED',''),
 ('FM_PILOT_UID',''),('FM_PILOT_UID','a/b'),('FM_GCP_PROJECT','other-project'),
 ('FM_FIRESTORE_DATABASE','(default)'),('FM_FIRESTORE_NAMESPACE','other'),('FM_DRIVE_ENABLED','true')])
def test_discovery_requires_explicit_inactive_state_and_exact_account_resources(field,value):
    env=discovery_environment();env[field]=value
    with pytest.raises(ConfigError):required_discovery_identity(env)


def test_discovery_factory_metadata_paused_auth_and_preserved_api_are_isolated(monkeypatch):
    import asyncio
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import httpx
    import pilot_cloud as cloud
    import pilot_oauth
    from fork_microscope.hosted.store import MemoryStore
    from pilot_budget import COLLECTION
    store=MemoryStore(); calls=[]; now=1791504000.
    env=discovery_environment()
    # These unverified form values have no role in discovery metadata.
    env.update(FM_PILOT_CIMD_URL='https://unverified.example/client',
               FM_PILOT_CALLBACK='https://unverified.example/callback')
    def forbidden(*args,**kwargs):raise AssertionError('Inactive capability was used')
    class Firebase:
        def verify(self,bearer):calls.append(('verify',bearer));return {'uid':bearer}
    monkeypatch.setattr(cloud.base,'_firestore',lambda _:store)
    monkeypatch.setattr(cloud.time,'time',lambda:now)
    monkeypatch.setattr(cloud,'adc_token_provider',lambda:forbidden)
    monkeypatch.setattr(cloud.base,'IdentityToolkitRevocation',lambda *a:SimpleNamespace(check=forbidden))
    monkeypatch.setattr(cloud.base,'FirebaseAuthenticator',lambda *a:Firebase())
    monkeypatch.setattr(cloud.base,'_google_id_token_provider',lambda *a:forbidden)
    monkeypatch.setattr(cloud.base,'ControllerClient',lambda *a:SimpleNamespace(call=forbidden,stream=forbidden))
    monkeypatch.setattr(cloud,'GoogleSecretVault',lambda *a:SimpleNamespace(put=forbidden,delete=forbidden,get=forbidden))
    monkeypatch.setattr(pilot_oauth,'PilotOAuthProvider',forbidden)
    monkeypatch.setattr(cloud,'PilotEvidenceLibrary',forbidden)
    monkeypatch.setattr(cloud,'create_hosted_mcp',forbidden)
    app=cloud.create_cloud_discovery_app(env)
    assert not hasattr(app.state,'pilot_mcp') and not hasattr(app.state,'pilot_library')
    assert isinstance(app.state.service,cloud.DiscoveryService)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=env['FM_DASHBOARD_ORIGIN']) as client:
            issuer=env['FM_DASHBOARD_ORIGIN']
            for path in ('/.well-known/oauth-protected-resource','/.well-known/oauth-protected-resource/mcp'):
                response=await client.get(path,headers={'Host':'untrusted.example'})
                assert response.status_code==200
                assert response.json()=={'resource':issuer+'/mcp','authorization_servers':[issuer],
                    'scopes_supported':['fork:manage'],'bearer_methods_supported':['header']}
            metadata=(await client.get('/.well-known/oauth-authorization-server')).json()
            assert metadata['issuer']==issuer and metadata['token_endpoint']==issuer+'/token'
            assert metadata['token_endpoint_auth_methods_supported']==['none']
            assert metadata['code_challenge_methods_supported']==['S256']
            assert metadata['client_id_metadata_document_supported'] is True
            assert metadata['authorization_response_iss_parameter_supported'] is True
            assert 'registration_endpoint' not in metadata and 'unverified' not in str(metadata)
            for method in ('GET','POST'):
                response=await client.request(method,'/mcp',content=b'not parsed',headers={'Authorization':'Bearer fake-sensitive-token'})
                assert response.status_code==401
                assert response.headers['www-authenticate']==('Bearer resource_metadata="'+issuer+
                    '/.well-known/oauth-protected-resource/mcp", scope="fork:manage"')
                for path in ('/authorize','/token','/revoke','/oauth/consent'):
                    response=await client.request(method,path+'?state=fake-sensitive-state',content=b'not parsed')
                    assert response.status_code==503 and response.json()=={'error':'temporarily_unavailable'}
                    assert 'set-cookie' not in response.headers and 'location' not in response.headers
                    assert response.headers['cache-control']=='no-store'
            assert calls==[] and store._data=={}
            assert (await client.post('/register',json={})).status_code==404
            assert (await client.get('/api/hosted/v1/me')).status_code==401
            assert (await client.get('/api/hosted/v1/me',headers={'Authorization':'Bearer bob'})).status_code==401
            # Even an old enabled/fresh budget must not enable any new work.
            month=datetime.fromtimestamp(now,timezone.utc).strftime('%Y-%m')
            store.transaction(lambda tx:tx.set(COLLECTION,'pilot_budget_control',dict(enabled=True,
                observed_at=now,estimated_total_usd=0,month=month)))
            headers={'Authorization':'Bearer alice'}
            assert (await client.get('/api/hosted/v1/me',headers=headers)).status_code==200
            for method,path in [('POST','/quotes'),('POST','/plans'),('POST','/plans/a/approve'),
                                ('POST','/sessions'),('POST','/jobs'),('PUT','/connections/runpod'),
                                ('DELETE','/connections/runpod')]:
                response=await client.request(method,'/api/hosted/v1'+path,json={'api_key':'fake-sensitive-key'},headers=headers)
                assert response.status_code==503 and 'fake-sensitive-key' not in response.text
            assert (await client.get('/api/hosted/v1/connections/drive',headers=headers)).json()=={'enabled':False,'connected':False}
            assert (await client.post('/api/hosted/v1/connections/drive/begin',headers=headers)).status_code==503
    asyncio.run(run())
    paths={getattr(route,'path',None) for route in app.routes}
    assert {'/api/hosted/v1/worker/enroll','/api/hosted/v1/worker/poll',
        '/api/hosted/v1/jobs/{resource_id}/cancel','/api/hosted/v1/sessions/{resource_id}/terminate'}<=paths


def test_discovery_recovery_preserves_owner_and_budget_checks():
    from types import SimpleNamespace
    from pilot_cloud import DiscoveryService
    from pilot_budget import PilotBudget
    from fork_microscope.hosted.service import HostedError
    from fork_microscope.hosted.store import MemoryStore
    calls=[];store=MemoryStore()
    service=DiscoveryService(SimpleNamespace(public=lambda *a,**k:calls.append(a) or {'ok':True}),
        PilotBudget(store,pilot_uid='alice',clock=lambda:1791504000.))
    for operation in ('me','session','get_job','artifacts','cancel','terminate'):
        assert service.public('alice',operation,{},'owned','request')=={'ok':True}
    assert [call[1] for call in calls]==['me','session','get_job','artifacts','cancel','terminate']
    with pytest.raises(HostedError):service.public('bob','cancel',{},'owned','request')
    for operation in ('quote','prepare_plan','approve_plan','start','job','runpod_put','runpod_delete'):
        with pytest.raises(HostedError) as error:service.public('alice',operation,{},'owned','request')
        assert error.value.status==503
    assert len(calls)==6


def test_discovery_disabled_oauth_does_not_read_body_or_parse_query():
    import asyncio
    from fastapi import FastAPI
    from pilot_cloud import mount_discovery
    app=mount_discovery(FastAPI(),issuer='https://pilot.example.test')
    async def run():
        for path in ('/token','/authorize','/revoke','/oauth/consent','/mcp'):
            messages=[]
            async def receive():raise AssertionError('Discovery must not read the request body')
            async def send(event):messages.append(event)
            await app({'type':'http','http_version':'1.1','method':'POST','scheme':'https',
                'path':path,'raw_path':path.encode(),'query_string':b'\xff\xfe',
                'root_path':'','headers':[],'server':('pilot.example.test',443),'client':None},receive,send)
            assert messages[0]['status']==(401 if path=='/mcp' else 503)
    asyncio.run(run())


@pytest.mark.parametrize('discovery',[False,True])
def test_dashboard_proxy_discovery_forwards_get_and_preserves_active_stateless_rule(monkeypatch,tmp_path,discovery):
    import hashlib
    import json
    from pathlib import Path
    import scripts.build_cloud_dashboard as build
    source=tmp_path/'source';source.mkdir()
    (source/'hosted-config.json').write_text(json.dumps({'enabled':True}))
    (source/'index.html').write_text('<!doctype html><title>Fixture</title>')
    (source/'build-manifest.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}))
    root=tmp_path/'workspace'
    deploy=root/'deploy/cloud-run';deploy.mkdir(parents=True)
    original=Path(__file__).resolve().parents[1]/'deploy/cloud-run'
    for name in ('Dockerfile','nginx.conf'):(deploy/name).write_bytes((original/name).read_bytes())
    monkeypatch.setattr(build,'ROOT',root)
    monkeypatch.setattr(build,'build',lambda:source)
    monkeypatch.setenv('FM_HOSTED_API_ORIGIN','https://api.example.test')
    monkeypatch.setenv('FM_PILOT_PROXY_ENABLED','true')
    monkeypatch.setenv('FM_PILOT_DISCOVERY','true' if discovery else 'false')
    output=build.prepare()
    config=(output/'nginx.conf').read_text()
    assert ('if ($request_method = GET) { return 405; }' in config) is (not discovery)
    for path in ('/mcp','/authorize','/token','/revoke','/oauth/consent',
                 '/.well-known/oauth-authorization-server','/.well-known/oauth-protected-resource/mcp'):
        assert 'location = '+path+' {' in config
    assert 'location /api/hosted/v1/' in config and 'proxy_ssl_verify on;' in config
    import_location='location = /api/hosted/v1/evidence/import {'
    assert (import_location in config) is (not discovery)
    if not discovery:
        location=config.split(import_location,1)[1].split('}',1)[0]
        assert 'client_max_body_size 16m;' in location
        assert 'client_body_timeout 10s;' in location
        assert 'access_log off;' in location and 'proxy_request_buffering off;' in location
    ordinary=config.split('location /api/hosted/v1/ {',1)[1].split('}',1)[0]
    assert 'client_max_body_size 64k;' in ordinary
    manifest=json.loads((output/'site/build-manifest.json').read_text())
    assert all(hashlib.sha256((output/'site'/name).read_bytes()).hexdigest()==sha for name,sha in manifest.items())
