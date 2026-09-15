"""Explicit-origin authentication for independently hosted GPU workers."""
import secrets
import re
from urllib.parse import urlsplit


def normalize_origin(value):
    parsed=urlsplit(value)
    if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
        raise ValueError('Allowed origins must be HTTP(S) origins without paths or credentials.')
    if parsed.scheme=='http' and parsed.hostname not in ('localhost','127.0.0.1','::1'):
        raise ValueError('Public dashboard origins must use HTTPS.')
    return f'{parsed.scheme}://{parsed.netloc}'


class WorkerAccess:
    def __init__(self, host='127.0.0.1', token='', origins=()):
        self.token=token
        self.origins={normalize_origin(origin) for origin in origins}
        self.remote=host not in ('127.0.0.1','localhost','::1') or bool(self.origins) or bool(token)
        if self.remote and (not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9._~-]{32,512}',token)):
            raise ValueError('Network access requires FORK_WORKER_TOKEN with 32–512 URL-safe ASCII characters.')

    def allowed_origin(self, origin):
        return origin in self.origins

    def authorize(self, host, origin, authorization, port):
        local_hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
        if not self.remote:
            return host in local_hosts and (not origin or origin==f'http://{host}')
        if origin and origin not in self.origins and origin not in {f'http://{host}',f'https://{host}'}:
            return False
        return bool(isinstance(authorization,str) and authorization.isascii() and secrets.compare_digest(authorization, 'Bearer '+self.token))
