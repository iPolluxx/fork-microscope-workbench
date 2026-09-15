import pytest
from fork_microscope.worker_connection import WorkerAccess, normalize_origin

def test_default_is_loopback_and_same_origin_only():
    access=WorkerAccess()
    assert access.authorize('127.0.0.1:8767',None,None,8767)
    assert not access.authorize('attacker.org:8767',None,None,8767)
    assert not access.authorize('127.0.0.1:8767','https://attacker.org',None,8767)

def test_remote_access_requires_token_and_explicit_origin():
    token='x'*32
    with pytest.raises(ValueError):WorkerAccess('0.0.0.0')
    with pytest.raises(ValueError):WorkerAccess(origins=['https://dashboard.example'])
    a=WorkerAccess('0.0.0.0',token,['https://dashboard.example'])
    assert a.authorize('worker.example','https://dashboard.example','Bearer '+token,8767)
    assert not a.authorize('worker.example','https://attacker.example','Bearer '+token,8767)
    assert not a.authorize('worker.example','https://dashboard.example','Bearer wrong',8767)
    assert not a.authorize('worker.example','https://dashboard.example',None,8767)
    assert a.authorize('worker.example',None,'Bearer '+token,8767) # CLI / tunnel

@pytest.mark.parametrize('origin',['*','https://example.org/path','https://user:pass@example.org','http://public.example','null'])
def test_disallow_unsafe_origins(origin):
    with pytest.raises(ValueError):normalize_origin(origin)

def test_authenticated_same_origin_worker_ui_and_non_ascii_header():
    a=WorkerAccess('0.0.0.0','t'*32,['https://dashboard.example'])
    assert a.authorize('127.0.0.1:8767','http://127.0.0.1:8767','Bearer '+'t'*32,8767)
    assert not a.authorize('worker.example','https://worker.example','Bearer snowman☃',8767)
    with pytest.raises(ValueError):WorkerAccess('0.0.0.0','☃'*40,[])


def test_http_cross_origin_status_prompt_write_and_preflight(tmp_path,monkeypatch):
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from fork_microscope import microscope_server
    from fork_microscope.live_service import LiveService
    service=LiveService(workspace_root=tmp_path)
    service._runtime=dict(cuda_available=False,gpu_name=None,system_memory_gb=16)
    monkeypatch.setattr(microscope_server,'LIVE',service)
    server=ThreadingHTTPServer(('127.0.0.1',0),microscope_server.Handler)
    server.worker_access=WorkerAccess('127.0.0.1','z'*32,['https://dashboard.example'])
    t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
    base=f'http://127.0.0.1:{server.server_port}'
    headers={'Origin':'https://dashboard.example','Authorization':'Bearer '+'z'*32}
    try:
        from scripts.build_dashboard import ASSETS
        for name in ASSETS:
            with urlopen(base+'/'+name) as response:assert response.status==200
        with pytest.raises(HTTPError) as err:urlopen(base+'/api/live/status')
        assert err.value.code==401
        with urlopen(Request(base+'/api/live/status',headers=headers)) as response:
            assert response.headers['Access-Control-Allow-Origin']=='https://dashboard.example'
            assert json.load(response)['model'] is None
        request=Request(base+'/api/live/prompt-set',method='OPTIONS',headers={'Origin':'https://dashboard.example','Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'authorization,content-type'})
        with urlopen(request) as response:assert response.status==204
        prompt=dict(id='one',title='First',prompt='Choose X',answers=['X'],mode='chat',max_tokens=16,seed=0)
        body=json.dumps(dict(name='Remote test',prompts=[prompt])).encode()
        with urlopen(Request(base+'/api/live/prompt-set',data=body,headers=headers|{'Content-Type':'application/json'})) as response:
            saved=json.load(response)['set']
        assert saved['name']=='Remote test' and service.model is None
        with pytest.raises(HTTPError) as err:
            urlopen(Request(base+'/api/live/prompt-sets',headers=headers|{'Origin':'https://blocked.example'}))
        assert err.value.code==401
    finally:
        server.shutdown();server.server_close();t.join(timeout=5)


def test_explicit_loopback_token_is_never_silently_ignored():
    access=WorkerAccess(token='t'*32)
    assert not access.authorize('127.0.0.1:8767',None,None,8767)
    assert access.authorize('127.0.0.1:8767',None,'Bearer '+'t'*32,8767)
    with pytest.raises(ValueError):WorkerAccess(token='too-short')
