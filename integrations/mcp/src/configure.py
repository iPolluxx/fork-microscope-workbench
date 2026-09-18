"""Initialize private local MCP profiles. Does not provision compute or contact a worker."""
import argparse,json,os,uuid
from pathlib import Path
from tools.profile import DEFAULT_LIMITS,load_profile,write_private,private

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    init=sub.add_parser('init');init.add_argument('--profile',required=True)
    worker=sub.add_parser('worker');worker.add_argument('--profile',required=True);worker.add_argument('--url',required=True);worker.add_argument('--token-file',required=True)
    a=parser.parse_args();path=Path(a.profile).expanduser().absolute()
    if a.action=='init':
        if path.exists():raise ValueError('Profile exists; refusing to overwrite.')
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);private(path.parent,True)
        if path.parent.resolve()!=path.parent:raise ValueError('Profile path cannot traverse symlinks.')
        p=dict(profile_id=uuid.uuid4().hex,worker_url=None,worker_token_file=None,limits=DEFAULT_LIMITS.copy())
        for key,name in [('evidence_dir','evidence'),('export_dir','exports'),('state_dir','state')]:
            d=path.parent/name;d.mkdir(mode=0o700,exist_ok=True);private(d,True);p[key]=str(d)
        write_private(path,p)
    else:
        os.environ['FORK_MICROSCOPE_PROFILE']=str(path);load_profile()
        from fork_microscope.workflow_cli import Client
        Client(a.url,token='configuration-validation-only')
        source=Path(a.token_file).expanduser().absolute();private(source)
        token=source.read_text().strip()
        if not token or '\n' in token or '\r' in token:raise ValueError('Token file must contain one nonempty token.')
        dest=path.parent/'worker-token';fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as f:f.write(token)
        os.chmod(dest,0o600)
        p=json.loads(path.read_text());p.update(worker_url=a.url.rstrip('/'),worker_token_file=str(dest));write_private(path,p)
    print(json.dumps({'profile':str(path),'worker_configured':a.action=='worker','message':'Private local configuration saved; no worker contacted.'}))

if __name__=='__main__':main()
