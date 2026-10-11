"""Fake-only OAuth HTTP/provider tests; no external auth or network listeners."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from starlette.applications import Starlette

from fork_microscope.hosted.store import MemoryStore
from hosted_pilot import PilotAccountLink, create_owner_resolver
from pilot_oauth import (COOKIE, PilotOAuthConfig, PilotOAuthProvider, PilotOAuthCleanup,
    _FirestoreOAuthCleanupStore, _POLICY, _COLLECTIONS, verified_firebase_identity)


ISSUER = "https://pilot.example.test"
CLIENT = "https://chatgpt.com/oauth/client.json"
REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"
UID = "firebase-pilot-uid"
FIREBASE = "fixture-firebase-token-sensitive"
FIREBASE_CONFIG = dict(apiKey='public-fixture-web-key',authDomain='pilot-project.firebaseapp.com',
    projectId='pilot-project',appId='1:fixture:web:fixture')
STATE = "fixture-client-state-sensitive"
VERIFIER = "x" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")


class Fixture:
    def __init__(self, **changes):
        self.now = int(time.time())
        self.store = MemoryStore()
        self.allowed = True
        self.valid_since = 0
        self.identities = {FIREBASE:{"uid":UID,"auth_time":900}}
        self.account_checks = []
        self.config = PilotOAuthConfig(issuer=ISSUER, resource=ISSUER+"/mcp", pilot_uid=UID,
            client_id=CLIENT, redirect_uri=REDIRECT, **changes)
        self.provider = self.restart()

    def firebase(self, token):
        return self.identities[token]

    def account(self, uid, auth_time):
        self.account_checks.append((uid,auth_time))
        return self.allowed and uid == UID and auth_time >= self.valid_since

    def restart(self):
        return PilotOAuthProvider(self.config, store=self.store, firebase_verify=self.firebase,
            check_account_access=self.account, firebase_config=FIREBASE_CONFIG, clock=lambda:self.now)

    def client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=Starlette(routes=self.provider.get_routes('/mcp'))),
            base_url=ISSUER, follow_redirects=False)


def auth_params(**changes):
    value = dict(client_id=CLIENT,redirect_uri=REDIRECT,response_type="code",scope="fork:manage",
        resource=ISSUER+"/mcp",code_challenge=CHALLENGE,code_challenge_method="S256",state=STATE)
    value.update(changes)
    return value


async def begin(client, **changes):
    response = await client.get('/authorize', params=auth_params(**changes))
    assert response.status_code == 302, response.text
    location = response.headers['location']
    assert location.startswith(ISSUER+'/oauth/consent?')
    assert 'Secure' in response.headers['set-cookie'] and 'HttpOnly' in response.headers['set-cookie']
    return parse_qs(urlsplit(location).query)['flow'][0]


async def consent(client, flow, *, approve=True, bearer=FIREBASE, origin=ISSUER):
    return await client.post('/oauth/consent',json={'flow':flow,'approve':approve},
        headers={'Origin':origin, 'Authorization':'Bearer '+bearer})


async def code(client):
    flow = await begin(client)
    approved = await consent(client,flow)
    assert approved.status_code == 200, approved.text
    query = parse_qs(urlsplit(approved.json()['redirect_url']).query)
    assert query['iss'] == [ISSUER] and query['state'] == [STATE]
    return query['code'][0]


async def exchange(client, value, **changes):
    data = dict(grant_type='authorization_code', client_id=CLIENT, code=value,
        redirect_uri=REDIRECT, code_verifier=VERIFIER, resource=ISSUER+'/mcp')
    data.update(changes)
    return await client.post('/token',data=data)


async def grant(client):
    issued = await exchange(client,await code(client))
    assert issued.status_code == 200, issued.text
    return issued.json()


def test_complete_http_flow_persists_hashes_restart_and_canonical_identity():
    fixture = Fixture()
    async def run():
        async with fixture.client() as client:
            tokens = await grant(client)
            assert tokens['scope'] == 'fork:manage' and tokens['expires_in'] == 300
            restarted = fixture.restart()
            verified = await restarted.load_access_token(tokens['access_token'])
            assert verified.subject == UID and verified.resource == ISSUER+'/mcp'
            owner = create_owner_resolver(PilotAccountLink(ISSUER,UID,UID,ISSUER+'/mcp'),
                access_check=restarted.access_check,clock=lambda:fixture.now)
            assert owner(verified) == UID
            serialized = json.dumps(fixture.store._data,default=str)
            for raw in (tokens['access_token'],tokens['refresh_token'],FIREBASE,STATE):
                assert raw not in serialized
            assert all(name.startswith('mcp_oauth_') for name in fixture.store._data)
            assert 'fork:manage' in serialized and fixture.account_checks
    asyncio.run(run())


def test_discovery_public_cimd_exact_issuer_and_no_dcr():
    fixture = Fixture()
    async def run():
        async with fixture.client() as client:
            value=(await client.get('/.well-known/oauth-authorization-server')).json()
            assert value['issuer']==ISSUER and value['token_endpoint_auth_methods_supported']==['none']
            assert value['client_id_metadata_document_supported'] is True
            assert value['authorization_response_iss_parameter_supported'] is True
            assert value['code_challenge_methods_supported']==['S256']
            assert 'registration_endpoint' not in value
            resource=(await client.get('/.well-known/oauth-protected-resource/mcp')).json()
            assert resource['authorization_servers']==[ISSUER] and resource['resource']==ISSUER+'/mcp'
            assert (await client.post('/register',json={})).status_code==404
            assert await fixture.provider.get_client('http://169.254.169.254/metadata') is None
            assert await fixture.provider.get_client(CLIENT+'?other=1') is None
    asyncio.run(run())


@pytest.mark.parametrize('changes',[{'client_id':'https://evil.example/client.json'},
    {'redirect_uri':'https://evil.example/callback'},{'resource':'https://evil.example/mcp'},
    {'scope':'other:read'},{'code_challenge_method':'plain'},{'code_challenge':'weak'}])
def test_authorize_rejects_unapproved_client_target_scope_or_pkce(changes):
    fixture=Fixture()
    async def run():
        async with fixture.client() as client:
            response=await client.get('/authorize',params=auth_params(**changes))
            if response.status_code==302:
                target=urlsplit(response.headers['location'])
                assert target.scheme+'://'+target.netloc+target.path==REDIRECT
                query=parse_qs(target.query)
                assert query['iss']==[ISSUER] and 'error' in query
            else:
                assert response.status_code==400
            assert not fixture.store._data.get('mcp_oauth_flows')
    asyncio.run(run())


def test_denial_has_issuer_and_does_not_mint_code_or_call_firebase():
    fixture=Fixture()
    fixture.provider.firebase_verify=lambda raw:pytest.fail('Denial must not authenticate')
    async def run():
        async with fixture.client() as client:
            response=await consent(client,await begin(client),approve=False)
            query=parse_qs(urlsplit(response.json()['redirect_url']).query)
            assert query=={'iss':[ISSUER],'state':[STATE],'error':['access_denied']}
            assert not fixture.store._data.get('mcp_oauth_codes')
    asyncio.run(run())


@pytest.mark.parametrize('variation',['cookie','state','origin','account','missing-token','expired'])
def test_consent_binds_browser_state_origin_exact_account_and_expiry(variation):
    fixture=Fixture()
    async def run():
        async with fixture.client() as client:
            flow=await begin(client)
            if variation=='cookie':client.cookies.clear()
            if variation=='state':
                current=client.cookies.get(COOKIE)
                body=json.loads(base64.urlsafe_b64decode(current+'='*(-len(current)%4)))
                body['state']='attacker-state'
                client.cookies.clear()
                client.cookies.set(COOKIE,base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip('='))
            if variation=='account':fixture.identities[FIREBASE]={'uid':'another-user','auth_time':900}
            if variation=='expired':fixture.now+=601
            response=await consent(client,flow,origin='https://evil.example' if variation=='origin' else ISSUER,
                bearer='unknown' if variation=='missing-token' else FIREBASE)
            assert response.status_code==400
            assert not fixture.store._data.get('mcp_oauth_codes')
    asyncio.run(run())


def test_consent_page_has_explicit_scope_no_token_storage_and_safe_policy():
    fixture=Fixture()
    async def run():
        async with fixture.client() as client:
            response=await client.get('/oauth/consent',params={'flow':await begin(client)})
            assert response.status_code==200
            assert 'fork:manage' in response.text and 'Allow ChatGPT access' in response.text
            assert 'inMemoryPersistence' in response.text
            assert 'localStorage' not in response.text and 'sessionStorage' not in response.text
            assert 'frame-ancestors' in response.headers['content-security-policy']
            policy=response.headers['content-security-policy']
            assert 'https://www.gstatic.com https://apis.google.com' in policy
            assert "'unsafe-inline'" not in policy and 'https:' not in policy.split('script-src ',1)[1].split(';',1)[0].replace('https://www.gstatic.com','').replace('https://apis.google.com','')
            assert response.headers['referrer-policy']=='no-referrer'
            assert 'no-store' in response.headers['cache-control']
    asyncio.run(run())


def test_consent_browser_signin_retry_back_expiry_and_denial_states():
    import subprocess
    from pathlib import Path
    fixture=Fixture()
    html=fixture.provider.consent_html('fm_fl_'+'A'*43,'fixture-nonce',fixture.now+600)
    script=html.split("<script type='module'",1)[1].split('>',1)[1].split('</script>',1)[0]
    helper=Path(__file__).with_name('pilot_consent_browser.mjs')
    result=subprocess.run(['node','--experimental-vm-modules',str(helper)],input=json.dumps({'script':script,'now':fixture.now}),text=True,capture_output=True,timeout=15)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['cases']==10


@pytest.mark.parametrize('changes',[{'code_verifier':'wrong'}, {'redirect_uri':'https://evil.example/callback'},
    {'client_id':'https://evil.example/client.json'},{'resource':'https://evil.example/mcp'}])
def test_code_exchange_binding_failure_does_not_consume_code(changes):
    fixture=Fixture()
    async def run():
        async with fixture.client() as client:
            authorization=await code(client)
            denied=await exchange(client,authorization,**changes)
            assert denied.status_code in (400,401)
            assert (await exchange(client,authorization)).status_code==200
    asyncio.run(run())


def test_code_replay_denied_and_revokes_issued_grant():
    fixture=Fixture()
    async def run():
        async with fixture.client() as client:
            authorization=await code(client)
            first=(await exchange(client,authorization)).json()
            assert await fixture.provider.load_access_token(first['access_token'])
            assert (await exchange(client,authorization)).status_code==400
            assert await fixture.provider.load_access_token(first['access_token']) is None
    asyncio.run(run())


def test_refresh_rotation_replay_and_per_grant_isolation():
    fixture=Fixture()
    async def run():
        async with fixture.client() as client:
            first=await grant(client)
            second=await grant(client)
            response=await client.post('/token',data={'grant_type':'refresh_token','client_id':CLIENT,
                'refresh_token':first['refresh_token'],'resource':ISSUER+'/mcp','scope':'fork:manage'})
            assert response.status_code==200
            rotated=response.json()
            assert rotated['refresh_token']!=first['refresh_token']
            replay=await client.post('/token',data={'grant_type':'refresh_token','client_id':CLIENT,
                'refresh_token':first['refresh_token'],'resource':ISSUER+'/mcp'})
            assert replay.status_code==400
            assert await fixture.provider.load_access_token(rotated['access_token']) is None
            assert await fixture.provider.load_access_token(second['access_token']) is not None
    asyncio.run(run())


def test_refresh_cannot_expand_scope_and_honors_original_firebase_auth_time():
    fixture=Fixture()
    async def run():
        async with fixture.client() as client:
            tokens=await grant(client)
            response=await client.post('/token',data={'grant_type':'refresh_token','client_id':CLIENT,
                'refresh_token':tokens['refresh_token'],'scope':'fork:manage admin','resource':ISSUER+'/mcp'})
            assert response.status_code==400
            fixture.valid_since=901
            denied=await client.post('/token',data={'grant_type':'refresh_token','client_id':CLIENT,
                'refresh_token':tokens['refresh_token'],'resource':ISSUER+'/mcp'})
            assert denied.status_code==400
            assert await fixture.provider.load_access_token(tokens['access_token']) is None
    asyncio.run(run())


def test_access_expiry_disabled_account_and_fail_closed_revocation_check():
    fixture=Fixture()
    async def run():
        async with fixture.client() as client:
            tokens=await grant(client)
            fixture.allowed=False
            assert await fixture.provider.load_access_token(tokens['access_token']) is None
            fixture.allowed=True
            def unavailable(uid,auth_time):raise RuntimeError(FIREBASE)
            fixture.provider.check_account_access=unavailable
            assert await fixture.provider.load_access_token(tokens['access_token']) is None
            fixture.provider.check_account_access=fixture.account
            fixture.now+=300
            assert await fixture.provider.load_access_token(tokens['access_token']) is None
    asyncio.run(run())


def test_http_revocation_is_idempotent_and_revokes_whole_grant():
    fixture=Fixture()
    async def run():
        async with fixture.client() as client:
            tokens=await grant(client)
            for _ in range(2):
                assert (await client.post('/revoke',data={'client_id':CLIENT,'token':tokens['refresh_token'],
                    'token_type_hint':'refresh_token'})).status_code==200
            assert await fixture.provider.load_access_token(tokens['access_token']) is None
    asyncio.run(run())


def test_concurrent_code_exchange_mints_at_most_one_pair():
    fixture=Fixture()
    async def prepare():
        async with fixture.client() as client:return await code(client)
    authorization=asyncio.run(prepare())
    async def exchange_direct():
        provider=fixture.restart()
        client=await provider.get_client(CLIENT)
        value=await provider.load_authorization_code(client,authorization)
        try:return await provider.exchange_authorization_code(client,value)
        except Exception:return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:asyncio.run(exchange_direct()),range(2)))
    assert sum(value is not None for value in results)==1
    assert len(fixture.store._data['mcp_oauth_access'])==1
    assert len(fixture.store._data['mcp_oauth_refresh'])==1


def test_quota_body_bounds_duplicate_fields_and_error_redaction():
    fixture=Fixture(authorizations_per_hour=1)
    async def run():
        async with fixture.client() as client:
            await begin(client)
            denied=await client.get('/authorize',params=auth_params())
            assert denied.status_code==302 and parse_qs(urlsplit(denied.headers['location']).query)['iss']==[ISSUER]
            assert len(fixture.store._data['mcp_oauth_flows'])==1
            for raw in ('client_id='+CLIENT+'&client_id='+CLIENT,'code='+FIREBASE*1000,
                        'grant_type='+FIREBASE):
                error=await client.post('/token',content=raw,headers={'Content-Type':'application/x-www-form-urlencoded'})
                assert error.status_code in (400,401) and FIREBASE not in error.text
    asyncio.run(run())


def test_verified_firebase_helper_calls_real_verifier_before_signed_claims():
    calls=[]
    class Auth:
        def verify(self,bearer):calls.append(bearer);return {'uid':UID}
    payload=base64.urlsafe_b64encode(json.dumps({'sub':UID,'auth_time':900}).encode()).decode().rstrip('=')
    raw='header.'+payload+'.signature'
    assert verified_firebase_identity(Auth(),raw)=={'uid':UID,'auth_time':900}
    assert calls==[raw]
    class Denied:
        def verify(self,bearer):raise ValueError(bearer)
    with pytest.raises(ValueError) as error:verified_firebase_identity(Denied(),raw)
    assert raw not in str(error.value)


def test_configuration_has_no_production_identity_or_client_fallback():
    config=Fixture().config
    for changes in ({'pilot_uid':''},{'issuer':'http://pilot.example.test'}, {'issuer':'https://key@pilot.example.test'},
        {'client_id':'https://evil.example/client.json'},{'redirect_uri':'https://chatgpt.com/other'},
        {'resource':'https://pilot.example.test/other'},{'access_ttl':86400}):
        with pytest.raises(ValueError):replace(config,**changes)
    assert UID not in repr(config) and FIREBASE not in repr(Fixture().provider)


def test_admission_denial_prevents_flow_code_refresh_and_replay_writes():
    fixture=Fixture()
    allow=[True]
    def admission():
        if not allow[0]:raise ValueError('budget unavailable')
    fixture.provider.admission=admission
    async def run():
        async with fixture.client() as client:
            tokens=await grant(client)
            flow=await begin(client)
            before=json.dumps(fixture.store._data,default=str,sort_keys=True)
            allow[0]=False
            assert (await consent(client,flow)).status_code==400
            assert (await client.get('/authorize',params=auth_params())).status_code==302
            assert (await client.post('/token',data={'grant_type':'refresh_token','client_id':CLIENT,
                'refresh_token':tokens['refresh_token'],'resource':ISSUER+'/mcp'})).status_code==400
            assert json.dumps(fixture.store._data,default=str,sort_keys=True)==before
            assert await fixture.provider.load_access_token(tokens['access_token']) is not None
            # Revocation is recovery and remains available while admissions pause.
            assert (await client.post('/revoke',data={'client_id':CLIENT,'token':tokens['refresh_token']})).status_code==200
            assert await fixture.provider.load_access_token(tokens['access_token']) is None
    asyncio.run(run())


def test_token_grants_require_explicit_exact_resource():
    fixture=Fixture()
    async def run():
        async with fixture.client() as client:
            authorization=await code(client)
            denied=await client.post('/token',data={'grant_type':'authorization_code','client_id':CLIENT,
                'code':authorization,'redirect_uri':REDIRECT,'code_verifier':VERIFIER})
            assert denied.status_code==400
            tokens=(await exchange(client,authorization)).json()
            assert (await client.post('/token',data={'grant_type':'refresh_token','client_id':CLIENT,
                'refresh_token':tokens['refresh_token']})).status_code==400
            assert (await client.post('/token',data={'grant_type':'refresh_token','client_id':CLIENT,
                'refresh_token':tokens['refresh_token'],'resource':ISSUER+'/mcp'})).status_code==200
    asyncio.run(run())


def test_retried_transaction_has_one_committed_code_and_token_pair():
    fixture=Fixture()
    original=fixture.store.transaction
    # Firestore may rerun a callback. Run its first attempt on a discarded copy,
    # then commit the second, without re-running random credential generation.
    import copy
    from fork_microscope.hosted.store import _MemoryTransaction
    def retry(fn):
        with fixture.store._lock:
            fn(_MemoryTransaction(copy.deepcopy(fixture.store._data)))
            return original(fn)
    fixture.store.transaction=retry
    async def run():
        async with fixture.client() as client:
            tokens=await grant(client)
            assert await fixture.provider.load_access_token(tokens['access_token'])
            assert len(fixture.store._data['mcp_oauth_flows'])==1
            assert len(fixture.store._data['mcp_oauth_codes'])==1
            assert len(fixture.store._data['mcp_oauth_grants'])==1
            assert len(fixture.store._data['mcp_oauth_access'])==1
            assert len(fixture.store._data['mcp_oauth_refresh'])==1
    asyncio.run(run())


def test_concurrent_refresh_mints_at_most_one_rotated_pair():
    fixture=Fixture()
    async def prepare():
        async with fixture.client() as client:return await grant(client)
    initial=asyncio.run(prepare())
    async def refresh_direct():
        provider=fixture.restart()
        client=await provider.get_client(CLIENT)
        loaded=await provider.load_refresh_token(client,initial['refresh_token'])
        try:return await provider.exchange_refresh_token(client,loaded,['fork:manage'])
        except Exception:return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:asyncio.run(refresh_direct()),range(2)))
    assert sum(value is not None for value in results)==1
    assert len(fixture.store._data['mcp_oauth_access'])==2
    assert len(fixture.store._data['mcp_oauth_refresh'])==2


def test_uncertain_code_exchange_never_reissues_and_replay_revokes_unknown_grant():
    fixture=Fixture()
    async def run():
        async with fixture.client() as client:
            authorization=await code(client)
            original=fixture.store.transaction
            failed=[False]
            def lose_response(fn):
                result=original(fn)
                if not failed[0] and fixture.store._data.get('mcp_oauth_access'):
                    failed[0]=True
                    raise TimeoutError('fixture lost response')
                return result
            fixture.store.transaction=lose_response
            first=await exchange(client,authorization)
            assert first.status_code==400
            assert (await exchange(client,authorization)).status_code==400
            assert len(fixture.store._data['mcp_oauth_access'])==1
            assert all(row['revoked'] for row in fixture.store._data['mcp_oauth_grants'].values())
    asyncio.run(run())


def test_store_outage_never_claims_revocation_or_mints_a_grant():
    fixture=Fixture()
    async def run():
        async with fixture.client() as client:
            tokens=await grant(client)
            def unavailable(fn):raise RuntimeError(FIREBASE)
            fixture.store.transaction=unavailable
            response=await client.post('/revoke',data={'client_id':CLIENT,'token':tokens['refresh_token']})
            assert response.status_code==503 and FIREBASE not in response.text
            assert await fixture.provider.load_access_token(tokens['access_token']) is None
    asyncio.run(run())


def test_recovery_refresh_keeps_existing_grant_available_when_new_work_stops():
    fixture=Fixture(refreshes_per_hour=1)
    allowed=[True]
    recovery=[]
    def admission():
        if not allowed[0]:raise ValueError('new work stopped')
    fixture.provider.admission=admission
    fixture.provider.recovery_admission=lambda:recovery.append('admitted')
    async def run():
        async with fixture.client() as client:
            initial=await grant(client)
            fixture.now+=301
            pending=await code(client)
            allowed[0]=False
            assert await fixture.provider.load_access_token(initial['access_token']) is None
            assert (await exchange(client,pending)).status_code==400
            assert (await client.get('/authorize',params=auth_params())).status_code==302
            response=await client.post('/token',data={'grant_type':'refresh_token','client_id':CLIENT,
                'refresh_token':initial['refresh_token'],'resource':ISSUER+'/mcp'})
            assert response.status_code==200 and recovery==['admitted']
            rotated=response.json()
            assert await fixture.provider.load_access_token(rotated['access_token']) is not None
            # Recovery does not widen scope or bypass the refresh rate cap.
            assert (await client.post('/token',data={'grant_type':'refresh_token','client_id':CLIENT,
                'refresh_token':rotated['refresh_token'],'resource':ISSUER+'/mcp'})).status_code==400
            assert len(fixture.store._data['mcp_oauth_grants'])==1
    asyncio.run(run())


@pytest.mark.parametrize('invalid',['expired','revoked','other-owner','account-revoked'])
def test_invalid_grant_cannot_use_recovery_refresh(invalid):
    fixture=Fixture()
    recovery=[]
    fixture.provider.recovery_admission=lambda:recovery.append('admitted')
    async def run():
        async with fixture.client() as client:
            tokens=await grant(client)
            fixture.provider.admission=lambda:False
            if invalid=='account-revoked':fixture.allowed=False
            elif invalid=='expired':fixture.now+=fixture.config.refresh_ttl+1
            else:
                def alter(tx):
                    key=next(iter(fixture.store._data['mcp_oauth_grants']))
                    row=tx.get('mcp_oauth_grants',key)
                    row.update({'revoked':True} if invalid=='revoked' else {'owner_uid':'someone-else'})
                    tx.set('mcp_oauth_grants',key,row)
                fixture.store.transaction(alter)
            response=await client.post('/token',data={'grant_type':'refresh_token','client_id':CLIENT,
                'refresh_token':tokens['refresh_token'],'resource':ISSUER+'/mcp'})
            assert response.status_code==400 and recovery==[]
    asyncio.run(run())


def test_exhausted_recovery_allowance_prevents_rotation_without_replay_writes():
    fixture=Fixture()
    async def run():
        async with fixture.client() as client:
            tokens=await grant(client)
            fixture.provider.recovery_admission=lambda:False
            before=json.dumps(fixture.store._data,default=str,sort_keys=True)
            response=await client.post('/token',data={'grant_type':'refresh_token','client_id':CLIENT,
                'refresh_token':tokens['refresh_token'],'resource':ISSUER+'/mcp'})
            assert response.status_code==400
            assert json.dumps(fixture.store._data,default=str,sort_keys=True)==before
    asyncio.run(run())


def test_all_new_oauth_rows_have_exact_cleanup_policy_owner_and_retention():
    fixture=Fixture()
    async def run():
        async with fixture.client() as client:await grant(client)
    asyncio.run(run())
    for rows in fixture.store._data.values():
        for row in rows.values():
            assert row['policy']==_POLICY and row['owner_uid']==UID
            assert row['delete_after']==datetime.fromtimestamp(fixture.now+30*86400,timezone.utc)


class MaintenanceStore:
    """Transactional fake with bounded candidate enumeration and true deletes."""
    namespace='hosted_v1'
    def __init__(self):
        self.memory=MemoryStore()
        self.queries=[]
        self.after_candidates=None
        self.fail_delete=False
    def transaction(self, fn):
        store=self
        class Transaction:
            def __init__(self,tx):self.tx=tx
            def get(self,*args):return self.tx.get(*args)
            def set(self,*args):return self.tx.set(*args)
            def delete(self,collection,key):
                if store.fail_delete:raise RuntimeError(FIREBASE)
                self.tx.data.get(collection,{}).pop(key,None)
        return self.memory.transaction(lambda tx:fn(Transaction(tx)))
    def candidates(self,collection,cutoff,after,limit):
        self.queries.append((collection,cutoff,after,limit))
        rows=self.memory._data.get(collection,{})
        candidates=[{'key':key,'delete_after':row['delete_after']} for key,row in rows.items()
                    if isinstance(row.get('delete_after'),datetime) and row['delete_after']<=cutoff]
        candidates.sort(key=lambda row:(row['delete_after'],row['key']))
        if after:
            candidates=[row for row in candidates if (row['delete_after'],row['key'])>(after['delete_after'],after['key'])]
        selected=candidates[:limit]
        if self.after_candidates:self.after_candidates(collection,selected)
        return selected


def maintenance():
    store=MaintenanceStore()
    now=datetime(2026,10,8,tzinfo=timezone.utc)
    def row(**fields):return dict(policy=_POLICY,owner_uid=UID,delete_after=now-timedelta(seconds=1),**fields)
    def put(key,value,collection='mcp_oauth_access'):
        store.transaction(lambda tx:tx.set(collection,key,value))
    clean=PilotOAuthCleanup(store,pilot_uid=UID,clock=lambda:now.timestamp(),batch_per_collection=2)
    return store,now,row,put,clean


def test_cleanup_deletes_only_expired_marked_same_owner_rows_and_resumes_past_others():
    store,now,row,put,clean=maintenance()
    put('a_unmarked',{'delete_after':now-timedelta(days=1)})
    put('b_other_owner',dict(row(),owner_uid='another-account'))
    put('c_expired',row())
    put('d_recent',dict(row(),delete_after=now+timedelta(seconds=1)))
    put('e_no_timestamp',{'policy':_POLICY,'owner_uid':UID})
    put('f_wrong_policy',dict(row(),policy='unrelated-policy'))
    put('unrelated',row(),'artifacts')
    assert clean()['removed_oauth_rows']==0
    # A new process resumes its persisted cursor instead of starving behind a/b.
    restarted=PilotOAuthCleanup(store,pilot_uid=UID,clock=lambda:now.timestamp(),batch_per_collection=2)
    assert restarted()['removed_oauth_rows']==1
    assert 'c_expired' not in store.memory._data['mcp_oauth_access']
    assert {'a_unmarked','b_other_owner','d_recent','e_no_timestamp','f_wrong_policy'}<=set(store.memory._data['mcp_oauth_access'])
    assert store.memory._data['artifacts']['unrelated']==row()
    assert all(collection in _COLLECTIONS.values() and limit==2 for collection,_,_,limit in store.queries)
    assert UID not in repr(clean)


@pytest.mark.parametrize('change',['renewal','owner','policy'])
def test_cleanup_fresh_transaction_defeats_renewal_or_identity_race(change):
    store,now,row,put,clean=maintenance()
    put('raced',row())
    def race(collection,candidates):
        if collection!='mcp_oauth_access' or not candidates:return
        changed=dict(row())
        if change=='renewal':changed['delete_after']=now+timedelta(days=30)
        elif change=='owner':changed['owner_uid']='another-account'
        else:changed.pop('policy')
        put('raced',changed)
    store.after_candidates=race
    assert clean()['removed_oauth_rows']==0
    assert 'raced' in store.memory._data['mcp_oauth_access']


def test_cleanup_transaction_retry_after_concurrent_renewal_preserves_row():
    import copy
    from fork_microscope.hosted.store import _MemoryTransaction
    store,now,row,put,clean=maintenance()
    put('raced',row())
    original=store.memory.transaction
    raced=[]
    def retry(fn):
        with store.memory._lock:
            discarded=copy.deepcopy(store.memory._data)
            fn(_MemoryTransaction(discarded))
            if not raced and 'raced' not in discarded.get('mcp_oauth_access',{}):
                # The first transaction read the expired row, but an intervening
                # renewal wins before commit. Firestore retries the transaction.
                raced.append(True)
                original(lambda tx:tx.set('mcp_oauth_access','raced',dict(row(),delete_after=now+timedelta(days=30))))
            return original(fn)
    store.memory.transaction=retry
    assert clean()['removed_oauth_rows']==0 and raced==[True]
    assert store.memory._data['mcp_oauth_access']['raced']['delete_after']>now


def test_cleanup_is_bounded_idempotent_and_does_not_claim_failed_delete():
    store,now,row,put,clean=maintenance()
    for kind,collection in _COLLECTIONS.items():
        for i in range(5):put(kind+'_'+str(i),row(),collection)
    store.fail_delete=True
    with pytest.raises(ValueError) as error:clean()
    assert FIREBASE not in str(error.value)
    store.fail_delete=False
    assert clean()=={'removed_oauth_rows':12,'examined_oauth_rows':12}
    assert clean()=={'removed_oauth_rows':12,'examined_oauth_rows':12}
    assert clean()=={'removed_oauth_rows':6,'examined_oauth_rows':6}
    assert clean()=={'removed_oauth_rows':0,'examined_oauth_rows':0}


def test_cleanup_adapter_uses_exact_namespace_bounded_single_field_query_and_transaction():
    from types import SimpleNamespace
    calls=[]
    class Ref:
        def __init__(self,name,key=None):self.name,self.key=name,key
        def document(self,key):return Ref(self.name,key)
        def where(self,**kw):calls.append(('where',self.name,kw));return self
        def order_by(self,value):calls.append(('order',value));return self
        def select(self,value):calls.append(('select',value));return self
        def start_after(self,value):calls.append(('after',value));return self
        def limit(self,value):calls.append(('limit',value));return self
        def stream(self):return []
        def get(self,*,transaction):calls.append(('get',self.name,self.key));return SimpleNamespace(exists=True,to_dict=lambda:{'test':True})
    class Client:
        def collection(self,name):calls.append(('collection',name));return Ref(name)
        def transaction(self):return SimpleNamespace(
            set=lambda ref,value:calls.append(('set',ref.name,ref.key)),
            delete=lambda ref:calls.append(('delete',ref.name,ref.key)))
    sdk=SimpleNamespace(FieldFilter=lambda *args:args,transactional=lambda fn:fn)
    adapter=_FirestoreOAuthCleanupStore(SimpleNamespace(namespace='hosted_v1',client=Client()),sdk)
    cutoff=datetime(2026,10,8,tzinfo=timezone.utc)
    assert adapter.candidates('mcp_oauth_access',cutoff,{'key':'hashed','delete_after':cutoff},7)==[]
    assert ('where','hosted_v1_mcp_oauth_access',{'filter':('delete_after','<=',cutoff)}) in calls
    assert ('limit',7) in calls
    def delete(tx):
        assert tx.get('mcp_oauth_access','hashed')=={'test':True}
        tx.delete('mcp_oauth_access','hashed')
    adapter.transaction(delete)
    assert ('delete','hosted_v1_mcp_oauth_access','hashed') in calls
    with pytest.raises(ValueError):adapter._collection('artifacts')
    with pytest.raises(ValueError):_FirestoreOAuthCleanupStore(SimpleNamespace(namespace='other',client=Client()),sdk)
