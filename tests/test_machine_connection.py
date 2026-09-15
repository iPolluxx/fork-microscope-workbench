import base64
import concurrent.futures
import json
import time
import pytest
from fork_microscope.machine_connection import Pairing, issue, write


def setup(tmp_path):
    (tmp_path/'token').write_text('t'*43)
    state=dict(port=8768,origin='https://dashboard.example',background='detached',share=False)
    result=issue(tmp_path,state)
    code=result['pairing_code'][4:]
    payload=json.loads(base64.urlsafe_b64decode(code+'='*(-len(code)%4)))
    return Pairing(tmp_path),payload,result


def test_single_use_and_no_long_lived_token_in_code(tmp_path):
    pairing,payload,result=setup(tmp_path)
    assert 'token' not in payload and 't'*43 not in result['pairing_code']
    assert pairing.exchange(payload['secret'],'https://dashboard.example',{'https://dashboard.example'})=={'token':'t'*43}
    with pytest.raises(ValueError):pairing.exchange(payload['secret'],None,set())


def test_expiry_origin_and_invalid_secret_do_not_redeem(tmp_path):
    pairing,payload,_=setup(tmp_path)
    with pytest.raises(ValueError):pairing.exchange(payload['secret'],'https://evil.example',{'https://dashboard.example'})
    with pytest.raises(ValueError):pairing.exchange('x'*43,None,set())
    value=json.loads((tmp_path/'pair.json').read_text());value['expires']=time.time()-1;write(tmp_path/'pair.json',value)
    with pytest.raises(ValueError):pairing.exchange(payload['secret'],None,set())


def test_reissue_invalidates_previous_code(tmp_path):
    pairing,old,_=setup(tmp_path)
    _,new,_=setup(tmp_path)
    with pytest.raises(ValueError):pairing.exchange(old['secret'],None,set())
    assert pairing.exchange(new['secret'],None,set())['token']=='t'*43


def test_concurrent_redemption_has_one_winner(tmp_path):
    pairing,payload,_=setup(tmp_path)
    def redeem(_):
        try:pairing.exchange(payload['secret'],None,set());return True
        except ValueError:return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(redeem,range(4)))==1


def test_http_pairing_and_protected_api(tmp_path):
    import threading,urllib.request,urllib.error
    from http.server import ThreadingHTTPServer
    from fork_microscope.microscope_server import Handler
    from fork_microscope.worker_connection import WorkerAccess
    pairing,payload,_=setup(tmp_path)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.pairing=pairing;server.worker_access=WorkerAccess(token='t'*43,origins=['https://dashboard.example'])
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}'
    try:
        with pytest.raises(urllib.error.HTTPError) as err:urllib.request.urlopen(url+'/api/live/runs')
        assert err.value.code==401
        req=urllib.request.Request(url+'/api/pair',data=json.dumps({'secret':payload['secret']}).encode(),headers={'Content-Type':'application/json','Origin':'https://dashboard.example'})
        with urllib.request.urlopen(req) as r:
            assert r.headers['Access-Control-Allow-Origin']=='https://dashboard.example'
            assert json.load(r)['token']=='t'*43
        with pytest.raises(urllib.error.HTTPError):urllib.request.urlopen(req)
    finally:server.shutdown();server.server_close();thread.join()


def test_repeat_start_reuses_worker_without_launching_process(tmp_path, monkeypatch):
    from fork_microscope import machine_connection as m
    from argparse import Namespace
    monkeypatch.setattr(m.Path, 'home', lambda: tmp_path)
    folder=tmp_path/'.local/state/fork-microscope/machine-8768'
    folder.mkdir(parents=True)
    (folder/'token').write_text('t'*43)
    state=dict(port=8768,origin='https://dashboard.example',background='detached',share=False,public_url=None)
    monkeypatch.setattr(m,'running',lambda _:state)
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**k:pytest.fail('must not launch a second process'))
    args=Namespace(port=8768,operation='start',dashboard_origin=state['origin'],share=False,public_url=None)
    first=m.launch(args);second=m.launch(args)
    assert first['worker_url']==second['worker_url']
    assert first['pairing_code']!=second['pairing_code']
    args.share=True
    with pytest.raises(ValueError,match='different settings'):m.launch(args)


def test_stop_refuses_reused_process_identity(tmp_path, monkeypatch):
    from fork_microscope import machine_connection as m
    from argparse import Namespace
    import os
    monkeypatch.setattr(m.Path,'home',lambda:tmp_path)
    monkeypatch.setattr(m,'running',lambda _:dict(pid=os.getpid(),background='detached'))
    monkeypatch.setattr(m.os,'kill',lambda *a:pytest.fail('must not signal an unrelated process'))
    with pytest.raises(ValueError,match='identity changed'):
        m.launch(Namespace(port=8768,operation='stop'))


def test_dashboard_origin_environment_default_and_cli_override(monkeypatch):
    import argparse
    from fork_microscope.machine_connection import parser
    monkeypatch.setenv('FORK_DASHBOARD_ORIGIN','https://my-dashboard.example')
    cli=argparse.ArgumentParser();parser(cli.add_subparsers(dest='command'))
    assert cli.parse_args(['machine','start']).dashboard_origin=='https://my-dashboard.example'
    assert cli.parse_args(['machine','start','--dashboard-origin','https://override.example']).dashboard_origin=='https://override.example'
