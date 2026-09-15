"""Prepare an isolated Cloud Run build context from the verified static manifest."""
from pathlib import Path
import hashlib
import json
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.build_dashboard import build, ROOT


def prepare():
    source=build()
    target=ROOT/'dist'/'cloud-run'
    if target.exists():
        if target.is_symlink():raise ValueError('Refusing a symlinked Cloud Run build directory.')
        shutil.rmtree(target)
    site=target/'site';site.mkdir(parents=True)
    manifest=json.loads((source/'build-manifest.json').read_text())
    for name,sha in manifest.items():
        path=source/name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:
            raise ValueError('Static asset differs from the build manifest: '+name)
        shutil.copy2(path,site/name)
    shutil.copy2(source/'build-manifest.json',site/'build-manifest.json')
    for name in ('Dockerfile','nginx.conf'):shutil.copy2(ROOT/'deploy'/'cloud-run'/name,target/name)
    (target/'.dockerignore').write_text('*\n!Dockerfile\n!nginx.conf\n!site/\n!site/**\n')
    return target

if __name__=='__main__':print(prepare())
