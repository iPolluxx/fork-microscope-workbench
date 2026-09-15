"""Personal worker startup and expiring, single-use pairing. No hosted relay."""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from fork_microscope.worker_connection import normalize_origin

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DASHBOARD = 'https://fork-microscope-wzyjs4vwsq-uc.a.run.app'

def write(path, value):
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w', opener=lambda p, flags: os.open(p, flags, 0o600)) as f:
        json.dump(value, f)
    tmp.replace(path)

class Pairing:
    def __init__(self, folder):
        self.folder = Path(folder)

    def exchange(self, secret, origin, allowed):
        if origin and origin not in allowed:
            raise ValueError('This website is not allowed to pair with the worker.')
        if not isinstance(secret, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', secret):
            raise ValueError('Invalid pairing code.')
        with open(self.folder/'pair.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                p = self.folder/'pair.json'
                value = json.loads(p.read_text())
                if time.time() >= value['expires'] or not secrets.compare_digest(value['digest'], hashlib.sha256(secret.encode()).hexdigest()):
                    raise ValueError('Pairing code is expired or invalid. Generate another with machine pair.')
                p.unlink()
                return {'token': (self.folder/'token').read_text().strip()}
            except FileNotFoundError:
                raise ValueError('Pairing code has already been used. Generate another with machine pair.') from None

def running(folder):
    try:
        state = json.loads((folder/'state.json').read_text())
        req = urllib.request.Request(f"http://127.0.0.1:{state['port']}/api/live/status", headers={'Authorization':'Bearer '+(folder/'token').read_text().strip()})
        with urllib.request.urlopen(req, timeout=2) as r:
            return state if r.status == 200 else None
    except (OSError, ValueError):
        return None

def public_url(folder, state):
    if state.get('share'):
        log = folder/'tunnel.log'
        matches = re.findall(r'https://[a-z0-9-]+\.trycloudflare\.com', log.read_text() if log.exists() else '')
        if not matches:
            raise ValueError('Tunnel is starting. Run machine pair again shortly, or inspect the machine log.')
        return matches[-1]
    return state.get('public_url') or f"http://127.0.0.1:{state['port']}"

def issue(folder, state):
    url = public_url(folder, state)
    secret = secrets.token_urlsafe(32)
    with open(folder/'pair.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        write(folder/'pair.json', {'digest':hashlib.sha256(secret.encode()).hexdigest(), 'expires':time.time()+600})
    code = 'FM1.'+base64.urlsafe_b64encode(json.dumps({'url':url,'secret':secret}, separators=(',', ':')).encode()).decode().rstrip('=')
    return {'status':'ready', 'dashboard':state['origin'], 'worker_url':url, 'pairing_code':code,
            'expires_in_seconds':600, 'background':state['background'],
            'note':'Paste the code into Connect a machine → Pair. It grants access to this worker. No model is loaded. '+('Temporary HTTPS address; re-pair after a tunnel restart. Keep this machine online.' if state.get('share') else 'Local addresses work only on this computer or through an SSH tunnel.'),
            'manage':{operation:f"fork-microscope machine {operation} --port {state['port']}" for operation in ('status','pair','stop')}}

def launch(args):
    if not 1 <= args.port <= 65535:raise ValueError('Port must be 1–65535.')
    folder = Path.home()/'.local/state/fork-microscope'/f'machine-{args.port}'
    folder.mkdir(parents=True, exist_ok=True);folder.chmod(0o700)
    with open(folder/'lifecycle.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        return _launch(args)

def _launch(args):
    if not 1 <= args.port <= 65535:
        raise ValueError('Port must be 1–65535.')
    folder = Path.home()/'.local/state/fork-microscope'/f'machine-{args.port}'
    folder.mkdir(parents=True, exist_ok=True); folder.chmod(0o700)
    state = running(folder)
    if args.operation == 'status':
        return {'running':bool(state), 'worker_url':public_url(folder,state) if state else None, 'log':str(folder/'worker.log')}
    if args.operation == 'stop':
        if not state and (folder/'state.json').exists():
            saved=json.loads((folder/'state.json').read_text())
            if saved.get('pid',0)>0 and Path(f"/proc/{saved['pid']}").exists():state=saved
        if state:
            pid = state['pid']
            # Never signal a reused PID belonging to another process.
            cmd = Path(f'/proc/{pid}/cmdline').read_bytes()
            if b'machine_connection' not in cmd or str(folder).encode() not in cmd:
                raise ValueError('Process identity changed; refusing to stop it.')
            if state['background'] == 'systemd':
                subprocess.run(['systemctl','--user','stop',f'fork-machine-{args.port}'], check=True)
            else:
                os.kill(pid, signal.SIGTERM)
        return {'stopped':True, 'note':'Saved evidence remains on disk. This does not terminate a rented VM or its billing.'}
    if args.operation == 'pair':
        if not state: raise ValueError('Worker is not running. Use machine start first.')
        return issue(folder,state)
    origin = normalize_origin(args.dashboard_origin)
    if state:
        if origin != state['origin'] or args.share != state['share'] or args.public_url != state.get('public_url'):
            raise ValueError('Worker already runs with different settings. Stop it before changing the connection, or use machine pair.')
        return issue(folder,state)
    if args.share and args.public_url: raise ValueError('Choose --share or --public-url, not both.')
    if args.public_url:
        args.public_url = normalize_origin(args.public_url)
        if not args.public_url.startswith('https://'):raise ValueError('A public worker address must use HTTPS.')
    if args.share and not shutil.which('cloudflared'):
        raise ValueError('--share needs cloudflared on PATH. Install Cloudflare Tunnel, or use your own HTTPS endpoint with --public-url.')
    import socket
    with socket.socket() as probe:
        try:probe.bind(('127.0.0.1',args.port))
        except OSError:raise ValueError('Port is already in use. Choose another --port; no existing service was changed.') from None
    token_file=folder/'token'
    if not token_file.exists():
        token_file.write_text(secrets.token_urlsafe(32));token_file.chmod(0o600)
    from fork_microscope.fork_cli import check_upstream
    check_upstream()
    managed = bool(shutil.which('systemd-run') and subprocess.run(['systemctl','--user','show-environment'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0)
    write(folder/'state.json',dict(port=args.port, origin=origin, share=args.share, public_url=args.public_url, background='systemd' if managed else 'detached', pid=0, cloudflared=shutil.which('cloudflared')))
    command=[sys.executable, '-m', 'fork_microscope.machine_connection', '--serve', str(folder)]
    if managed:
        subprocess.run(['systemd-run','--user',f'--unit=fork-machine-{args.port}','--collect','--property=Restart=on-failure','--property=RestartSec=3',f'--working-directory={ROOT}',*command],check=True,stdout=subprocess.DEVNULL)
    else:
        with open(folder/'worker.log','ab') as log:subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    for _ in range(60):
        state=running(folder)
        if state:
            try:return issue(folder,state)
            except ValueError:pass
        time.sleep(1)
    raise ValueError(f'Startup is still pending or failed. Use machine status / machine pair. Logs: {folder}/worker.log (systemd: journalctl --user -u fork-machine-{args.port}).')

def serve(folder):
    folder=Path(folder);state=json.loads((folder/'state.json').read_text());state['pid']=os.getpid();write(folder/'state.json',state)
    os.environ['FORK_WORKER_TOKEN']=(folder/'token').read_text().strip()
    tunnel=None
    if state['share']:
        # A fresh file prevents issuing codes pointing at a previous tunnel.
        (folder/'tunnel.log').write_text('')
        log=open(folder/'tunnel.log','ab')
        tunnel=subprocess.Popen([state['cloudflared'],'tunnel','--url',f"http://127.0.0.1:{state['port']}",'--no-autoupdate'],stdout=log,stderr=log)
    from http.server import ThreadingHTTPServer
    from fork_microscope import microscope_server as app
    from fork_microscope.worker_connection import WorkerAccess
    server=ThreadingHTTPServer(('127.0.0.1',state['port']),app.Handler)
    server.worker_access=WorkerAccess('127.0.0.1',os.environ['FORK_WORKER_TOKEN'],[state['origin']])
    server.pairing=Pairing(folder)
    def stop(*_):raise SystemExit()
    signal.signal(signal.SIGTERM,stop)
    server.timeout=1
    try:
        while True:
            server.handle_request()
            if tunnel and tunnel.poll() is not None:
                log.close()
                # Failed tunnels reconnect with a new temporary address.
                time.sleep(2)
                log=open(folder/'tunnel.log','wb')
                tunnel=subprocess.Popen([state['cloudflared'],'tunnel','--url',f"http://127.0.0.1:{state['port']}",'--no-autoupdate'],stdout=log,stderr=log)
    finally:
        server.server_close()
        if tunnel:
            tunnel.terminate()
            try:tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:tunnel.kill()

def parser(sub):
    p=sub.add_parser('machine',help='Start a background worker and pair it with the dashboard')
    p.add_argument('operation',choices=['start','pair','status','stop'])
    p.add_argument('--port',type=int,default=8768)
    p.add_argument('--dashboard-origin',default=os.environ.get('FORK_DASHBOARD_ORIGIN', DEFAULT_DASHBOARD), help='Dashboard origin (default: FORK_DASHBOARD_ORIGIN or the public Fork Microscope site)')
    p.add_argument('--share',action='store_true',help='Create a temporary HTTPS Cloudflare tunnel for phone/remote access')
    p.add_argument('--public-url',help='Existing HTTPS endpoint forwarding to the worker port')

if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--serve':serve(sys.argv[2])
