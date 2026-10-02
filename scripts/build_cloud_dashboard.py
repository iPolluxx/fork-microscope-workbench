"""Prepare an isolated Cloud Run build context from the verified static manifest."""
from pathlib import Path
import hashlib
import json
import shutil
import sys
import os
import re
from urllib.parse import urlsplit
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
    upstream=os.environ.get('FM_HOSTED_API_ORIGIN','')
    hosted=json.loads((source/'hosted-config.json').read_text())
    if hosted.get('enabled') and not upstream:
        raise ValueError('Hosted compute is enabled: set FM_HOSTED_API_ORIGIN to preserve the API proxy when deploying.')
    if upstream:
        p=urlsplit(upstream)
        if p.scheme!='https' or not re.fullmatch(r'[a-z0-9.-]+',p.netloc) or p.path not in ('','/') or p.query or p.fragment:
            raise ValueError('FM_HOSTED_API_ORIGIN must be a plain HTTPS service origin.')
        config=(target/'nginx.conf').read_text()
        proxy='location /api/hosted/v1/ {\n            access_log off;\n            client_max_body_size 64k;\n            proxy_pass https://HOST/api/hosted/v1/;\n            proxy_set_header Host HOST;\n            proxy_ssl_server_name on;\n            proxy_ssl_name HOST;\n            proxy_ssl_verify on;\n            proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;\n            proxy_buffering off;\n            proxy_request_buffering off;\n            proxy_read_timeout 90s;\n            add_header Cache-Control "no-store" always;\n        }'.replace('HOST',p.netloc)
        config=config.replace('location /api/ { return 404; }',proxy+'\n        location /api/ { return 404; }')
        (target/'nginx.conf').write_text(config)
    (target/'.dockerignore').write_text('*\n!Dockerfile\n!nginx.conf\n!site/\n!site/**\n')
    return target

if __name__=='__main__':print(prepare())
