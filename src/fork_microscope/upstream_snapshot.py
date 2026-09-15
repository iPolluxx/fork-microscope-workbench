"""Verify a pinned checkout or a content-verified container snapshot without Git secrets."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

MANIFEST = '.fork-source.json'


def _hash(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def _file(root,name):
    if not isinstance(name,str):raise RuntimeError('Invalid upstream snapshot path.')
    p=PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or not p.parts or str(p)!=name:
        raise RuntimeError('Invalid upstream snapshot path.')
    path=root.joinpath(*p.parts)
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise RuntimeError(f'Upstream snapshot file unavailable: {name}')
    return path


def write_snapshot(root, expected):
    root=Path(root)
    actual=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    if actual!=expected:raise RuntimeError('Refusing to snapshot a different upstream revision.')
    subprocess.run(['git','-C',str(root),'diff','--quiet','HEAD','--'],check=True)
    files=subprocess.check_output(['git','-C',str(root),'ls-files','-z']).decode().strip('\0').split('\0')
    data=dict(schema_version=1,commit=expected,files={name:_hash(_file(root,name)) for name in files})
    (root/MANIFEST).write_text(json.dumps(data,sort_keys=True),encoding='utf-8')
    return data


def verify_snapshot(root, expected):
    root=Path(root)
    try:data=json.loads((root/MANIFEST).read_text())
    except (OSError,ValueError) as exc:raise RuntimeError('No verifiable upstream snapshot. Use a recursive clone or the tested worker container.') from exc
    if (type(data) is not dict or data.get('schema_version')!=1 or data.get('commit')!=expected
            or type(data.get('files')) is not dict or not data['files']):
        raise RuntimeError('Upstream snapshot provenance does not match the tested pin.')
    for name,digest in data['files'].items():
        if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest) or _hash(_file(root,name))!=digest:
            raise RuntimeError(f'Upstream snapshot content differs: {name}')


def verify_upstream(root, expected):
    root=Path(root)
    if (root/'.git').exists():
        actual=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
        if actual!=expected:raise RuntimeError('Upstream revision differs from the tested pin. Run git submodule update --init.')
        if subprocess.run(['git','-C',str(root),'diff','--quiet','HEAD','--']).returncode:
            raise RuntimeError('Upstream source has local changes. Restore the pinned checkout before claiming a verified build.')
    else:verify_snapshot(root,expected)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('root');parser.add_argument('--revision',required=True);parser.add_argument('--write',action='store_true')
    args=parser.parse_args()
    (write_snapshot if args.write else verify_snapshot)(args.root,args.revision)
