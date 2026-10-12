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
    discovery=os.environ.get('FM_PILOT_DISCOVERY') == 'true'
    if discovery and os.environ.get('FM_PILOT_PROXY_ENABLED') != 'true':
        raise ValueError('Discovery requires the explicit pilot proxy allowlist.')
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
    if os.environ.get('FM_PILOT_PROXY_ENABLED') == 'true':
        hosted['pilot']=True
        (site/'hosted-config.json').write_text(json.dumps(hosted,indent=2)+'\n')
        manifest['hosted-config.json']=hashlib.sha256((site/'hosted-config.json').read_bytes()).hexdigest()
        (site/'build-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    if hosted.get('enabled') and not upstream:
        raise ValueError('Hosted compute is enabled: set FM_HOSTED_API_ORIGIN to preserve the API proxy when deploying.')
    if upstream:
        p=urlsplit(upstream)
        if p.scheme!='https' or not re.fullmatch(r'[a-z0-9.-]+',p.netloc) or p.path not in ('','/') or p.query or p.fragment:
            raise ValueError('FM_HOSTED_API_ORIGIN must be a plain HTTPS service origin.')
        config=(target/'nginx.conf').read_text()
        proxy='location /api/hosted/v1/ {\n            access_log off;\n            client_max_body_size 64k;\n            proxy_pass https://HOST/api/hosted/v1/;\n            proxy_set_header Host HOST;\n            proxy_ssl_server_name on;\n            proxy_ssl_name HOST;\n            proxy_ssl_verify on;\n            proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;\n            proxy_buffering off;\n            proxy_request_buffering off;\n            proxy_read_timeout 90s;\n            add_header Cache-Control "no-store" always;\n        }'.replace('HOST',p.netloc)
        config=config.replace('location /api/ { return 404; }',proxy+'\n        location /api/ { return 404; }')
        if os.environ.get('FM_PILOT_PROXY_ENABLED') == 'true':
            # Exact routes only. Existing API and static assets remain separate.
            # Per-address throttles reduce abuse; they are not a monthly cost cap.
            config=config.replace('http {','http {\n    limit_req_zone $binary_remote_addr zone=fm_pilot:1m rate=2r/s;')
            paths=('/mcp','/authorize','/token','/revoke','/oauth/consent',
                   '/.well-known/oauth-authorization-server',
                   '/.well-known/oauth-protected-resource','/.well-known/oauth-protected-resource/mcp')
            additions=[]
            if not discovery:
                # File import alone accepts larger bounded bodies. Ordinary API/MCP
                # mutations retain 64 KiB, and the API authenticates before reading.
                additions.append(('location = /api/hosted/v1/evidence/import {\n'
                    '            access_log off;\n'
                    '            limit_req zone=fm_pilot burst=2 nodelay;\n'
                    '            client_max_body_size 16m;\n'
                    '            client_body_timeout 10s;\n'
                    '            proxy_pass https://HOST;\n'
                    '            proxy_set_header Host HOST;\n'
                    '            proxy_ssl_server_name on;\n'
                    '            proxy_ssl_name HOST;\n'
                    '            proxy_ssl_verify on;\n'
                    '            proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;\n'
                    '            proxy_request_buffering off;\n'
                    '            proxy_read_timeout 65s;\n'
                    '            add_header Cache-Control "no-store" always;\n'
                    '        }').replace('HOST',p.netloc))
            for path in paths:
                rule=('location = PATH {\n'
                      '            access_log off;\n'
                      '            limit_req zone=fm_pilot burst=8 nodelay;\n'
                      '            client_max_body_size 64k;\n'
                      '            proxy_pass https://HOST;\n'
                      '            proxy_set_header Host HOST;\n'
                      '            proxy_ssl_server_name on;\n'
                      '            proxy_ssl_name HOST;\n'
                      '            proxy_ssl_verify on;\n'
                      '            proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;\n'
                      '            proxy_read_timeout 65s;\n'
                      '            proxy_send_timeout 65s;\n'
                      '            proxy_buffering on;\n'
                      '            add_header Cache-Control "no-store" always;\n'
                      '            add_header Referrer-Policy "no-referrer" always;\n'
                      '        }').replace('PATH',path).replace('HOST',p.netloc)
                if path == '/mcp' and not discovery:
                    rule=rule.replace('access_log off;', 'access_log off;\n            if ($request_method = GET) { return 405; }')
                additions.append(rule)
            config=config.replace('location /api/ { return 404; }','\n        '.join(additions)+'\n        location /api/ { return 404; }')
        (target/'nginx.conf').write_text(config)
    (target/'.dockerignore').write_text('*\n!Dockerfile\n!nginx.conf\n!site/\n!site/**\n')
    return target

if __name__=='__main__':print(prepare())
