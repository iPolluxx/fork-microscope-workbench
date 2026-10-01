"""Worker-only immutable evidence delivery; never exposes execution or model APIs.

Device downloads use expiring, per-file bearer capabilities, not worker credentials.
Drive upload session URLs are secrets; no URLs or authorization headers are logged.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MAX_BYTES = 64 * 1024 * 1024
ID = re.compile(r"^[a-f0-9]{32}$")


class EvidenceDelivery:
    def __init__(self, root, *, origin, deadline, clock=time.time):
        p = urlsplit(origin)
        if p.scheme not in ('https', 'http') or p.path not in ('', '/') or p.query or p.fragment or p.username or (p.scheme == 'http' and p.hostname not in ('localhost', '127.0.0.1')):
            raise ValueError('Use an exact HTTPS dashboard origin.')
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.origin = origin.rstrip('/')
        self.deadline = deadline
        self.clock = clock
        self.lock = threading.RLock()
        self.records = {}
        manifest = self.root / 'capabilities.json'
        if manifest.exists():
            records = json.loads(manifest.read_text())
            for key, record in records.items():
                if not ID.fullmatch(key) or record.get('id') != key:
                    raise ValueError('Invalid saved artifact capability.')
                path = self.root / (key + '.json')
                if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
                    raise ValueError('Invalid saved artifact file.')
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != record['sha256'] or len(data) != record['size_bytes']:
                    raise ValueError('Saved artifact integrity failed.')
                if record['expires_at'] > self.clock():
                    self.records[key] = record

    def add(self, artifact_id, bundle):
        if not ID.fullmatch(artifact_id):
            raise ValueError('Invalid artifact identifier.')
        data = json.dumps(bundle, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        if len(data) > MAX_BYTES:
            raise ValueError('Bundle exceeds the 64 MiB portable evidence limit.')
        digest = hashlib.sha256(data).hexdigest()
        with self.lock:
            old = self.records.get(artifact_id)
            if old:
                if old['sha256'] != digest:
                    raise ValueError('Artifact is immutable.')
                return dict(old)
            path = self.root / (artifact_id + '.json')
            tmp = path.with_suffix('.tmp')
            tmp.write_bytes(data)
            tmp.replace(path)
            record = dict(id=artifact_id, sha256=digest, size_bytes=len(data),
                          download_token=secrets.token_urlsafe(32), expires_at=self.deadline)
            self.records[artifact_id] = record
            manifest = self.root / 'capabilities.json'
            fd = os.open(manifest.with_suffix('.tmp'), os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump(self.records, f, allow_nan=False)
            manifest.with_suffix('.tmp').replace(manifest)
            return dict(record)

    def authorized(self, artifact_id, authorization, origin):
        with self.lock:
            item = self.records.get(artifact_id)
            if not item or self.clock() >= item['expires_at'] or (origin and origin != self.origin):
                return None
            expected = 'Bearer ' + item['download_token']
            if not isinstance(authorization, str) or not authorization.isascii() or not hmac.compare_digest(expected, authorization):
                return None
            return self.root / (artifact_id + '.json')

    def server(self, host='0.0.0.0', port=8780):
        delivery = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass  # paths and capabilities must not enter provider logs

            def do_OPTIONS(self):
                if self.headers.get('Origin') != delivery.origin:
                    self.send_error(403)
                    return
                self.send_response(204)
                self.send_header('Access-Control-Allow-Origin', delivery.origin)
                self.send_header('Vary', 'Origin')
                self.send_header('Access-Control-Allow-Headers', 'Authorization')
                self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
                self.end_headers()

            def do_GET(self):
                match = re.fullmatch(r'/bundles/([a-f0-9]{32})', self.path)
                path = delivery.authorized(match[1], self.headers.get('Authorization'), self.headers.get('Origin')) if match else None
                if path is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(path.stat().st_size))
                self.send_header('Content-Disposition', 'attachment; filename="investigation.json"')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Access-Control-Allow-Origin', delivery.origin)
                self.send_header('Vary', 'Origin')
                self.end_headers()
                with path.open('rb') as f:
                    while block := f.read(256 * 1024):
                        self.wfile.write(block)

        return ThreadingHTTPServer((host, port), Handler)


def upload_drive(path, session_url, *, opener=urlopen, clock=time.time, deadline=float('inf')):
    """Upload/resume a bounded file to a Google-issued per-file session.

Only Google's upload endpoint is accepted. 308 is handled as progress, not as a
redirect to another host. Do not put this session URL in a public job record.
"""
    from urllib.error import HTTPError, URLError
    from urllib.request import HTTPRedirectHandler, build_opener
    parsed = urlsplit(session_url)
    if parsed.scheme != 'https' or parsed.netloc != 'www.googleapis.com' or not parsed.path.startswith('/upload/drive/v3/files'):
        raise ValueError('Invalid Drive upload session.')
    if opener is urlopen:
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = build_opener(NoRedirect).open
    path = Path(path)
    size = path.stat().st_size
    if not 0 < size <= MAX_BYTES:
        raise ValueError('Invalid bundle size.')
    offset, failures, query_status = 0, 0, False
    while clock() < deadline:
        if query_status:
            body = b''
            content_range = f'bytes */{size}'
        else:
            with path.open('rb') as f:
                f.seek(offset)
                body = f.read(1024 * 1024)
            content_range = f'bytes {offset}-{offset + len(body) - 1}/{size}'
        req = Request(session_url, data=body, method='PUT', headers={
            'Content-Type': 'application/json', 'Content-Length': str(len(body)), 'Content-Range': content_range})
        try:
            response = opener(req, timeout=min(30, max(1, deadline - clock())))
        except HTTPError as exc:
            response = exc
        except (URLError, TimeoutError, OSError):
            failures += 1
            if failures > 5:
                raise RuntimeError('Drive upload interrupted; completed local evidence is retained.') from None
            query_status = True
            continue
        with response:
            status = response.status
            if status in (200, 201):
                value = json.loads(response.read(1024 * 1024))
                if not isinstance(value.get('id'), str):
                    raise RuntimeError('Drive did not return an artifact identifier.')
                return value
            if status == 308:
                match = re.fullmatch(r'bytes=0-(\d+)', response.headers.get('Range', ''))
                next_offset = int(match[1]) + 1 if match else 0
                if not 0 <= next_offset <= size:
                    raise RuntimeError('Invalid Drive upload progress.')
                failures = failures + 1 if next_offset <= offset else 0
                if failures > 5:
                    raise RuntimeError('Drive upload made no progress.')
                offset, query_status = next_offset, next_offset == size
                continue
            if status >= 500:
                failures += 1
                if failures <= 5:
                    query_status = True
                    continue
            raise RuntimeError('Drive upload failed; reconnect storage and retry before the worker deadline.')
    raise RuntimeError('Evidence export deadline reached.')
