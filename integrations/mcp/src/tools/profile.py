"""Private local profile paths; no worker credentials accepted through MCP arguments."""
import json
import os
from pathlib import Path
import stat
import tempfile

DEFAULT_LIMITS = {'max_seconds':3600,'max_samples':10000,'max_generated_tokens':10000000}

def private(path: Path, directory=False):
    if path.is_symlink(): raise ValueError('Private profile paths cannot be symlinks.')
    info=path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077: raise ValueError('Profile files and directories must be owned by this user and private (0700/0600).')
    if directory != stat.S_ISDIR(info.st_mode): raise ValueError('Invalid private profile path type.')
    if not directory and not stat.S_ISREG(info.st_mode): raise ValueError('Expected a regular private file.')

def write_private(path: Path, value):
    fd,name=tempfile.mkstemp(prefix='.write-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f: json.dump(value,f,allow_nan=False);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def load_profile():
    raw=os.environ.get('FORK_MICROSCOPE_PROFILE','')
    path=Path(raw)
    if not raw or not path.is_absolute():raise ValueError('Set FORK_MICROSCOPE_PROFILE to an absolute private profile JSON path.')
    private(path);root=path.parent
    private(root,True)
    if root.resolve()!=root:raise ValueError('Profile directory cannot traverse symlinks.')
    p=json.loads(path.read_text())
    required={'profile_id','evidence_dir','export_dir','state_dir','worker_url','worker_token_file','limits'}
    if set(p)!=required:raise ValueError('Invalid profile fields; use configure.py init.')
    if not isinstance(p['profile_id'],str) or len(p['profile_id'])!=32 or any(c not in '0123456789abcdef' for c in p['profile_id']):raise ValueError('Invalid profile identity.')
    for key in ('evidence_dir','export_dir','state_dir'):
        child=Path(p[key])
        if not child.is_absolute() or child.parent!=root or child==root:raise ValueError('Private storage must be separate direct children of the profile directory.')
        private(child,True);p[key]=child
    if len({p[k] for k in ('evidence_dir','export_dir','state_dir')})!=3:raise ValueError('Private storage directories must be distinct.')
    if set(p['limits'])!=set(DEFAULT_LIMITS) or any(type(v)is not int or not 1<=v<=10**9 for v in p['limits'].values()):raise ValueError('Invalid profile resource ceilings.')
    if bool(p['worker_url'])!=bool(p['worker_token_file']):raise ValueError('Configure both worker origin and token file.')
    if p['worker_url']:
        if not isinstance(p['worker_url'],str):raise ValueError('Invalid worker origin.')
        from fork_microscope.workflow_cli import Client
        Client(p['worker_url'],token='configuration-validation-only')
        p['worker_url']=p['worker_url'].rstrip('/')
        token=Path(p['worker_token_file'])
        if token.parent!=root or not token.is_absolute():raise ValueError('Worker token must be stored privately beside profile.')
        private(token);p['worker_token_file']=token
    else:p['worker_url']=None;p['worker_token_file']=None
    return p
