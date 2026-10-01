"""Assembly of the two hosted deployables from environment configuration.

Run (same image, two Cloud Run services with different service accounts and env):

    uvicorn fork_microscope.hosted.bootstrap:create_public_app --factory      # public API
    uvicorn fork_microscope.hosted.bootstrap:create_controller_app --factory  # private controller

Nothing here talks to a cloud at import time. Optional SDKs (FastAPI, google-auth,
google-cloud-firestore) are imported lazily; there is no implicit fake or
unauthenticated fallback. ``create_test_apps`` is the only place fakes are wired, and only
from explicitly injected objects.

Environment (names only are ever reported on validation errors, never values).

Both roles
  FM_HOSTED_ENV                     production | staging
  FM_GCP_PROJECT                    project id for Firestore and Secret Manager
  FM_FIRESTORE_DATABASE             optional, default (default)
  FM_FIRESTORE_NAMESPACE            optional, default hosted_v1
  FM_PUBLIC_URL                     https origin of the public API (also what workers call)
  FM_DASHBOARD_ORIGIN               https origin serving hosted.html
  FM_CONTROLLER_OIDC_AUDIENCE       exact ``aud`` of internal ID tokens
  FM_DRIVE_CLIENT_ID                OAuth web client id
  FM_DRIVE_REDIRECT_URI             must equal FM_PUBLIC_URL + /api/hosted/v1/connections/drive/callback
  FM_DRIVE_CLIENT_SECRET            OAuth client secret (Cloud Run secret env), or
  FM_DRIVE_CLIENT_SECRET_FILE       absolute path of a mounted secret file (exactly one of the two)
Public only
  FM_FIREBASE_PROJECT_ID            Firebase project id (strict iss/aud)
  FM_INVITED_EMAILS                 comma/space separated allowlist
  FM_CONTROLLER_URL                 https origin of the private controller
  FM_DRIVE_POST_AUTH_URL            exact hosted.html URL users return to after consent
  FM_OAUTH_COOKIE_NAME              optional, default __Host-fm_drive_oauth (use __session behind Firebase Hosting)
Controller only
  FM_PUBLIC_SERVICE_ACCOUNT_EMAIL     identity allowed to call /internal/* (not reconcile)
  FM_SCHEDULER_SERVICE_ACCOUNT_EMAIL  identity allowed to call /internal/reconcile only
  FM_WORKER_IMAGE                     immutable ``name@sha256:<digest>``
  FM_DISK_USD_PER_GB_HOUR, FM_DISK_RATE_SOURCE   explicit disk price and its provenance
  optional (bounded): FM_DISK_GB 30, FM_MAX_SESSION_SECONDS 3600, FM_QUOTE_TTL_SECONDS 120,
  FM_STARTUP_ALLOWANCE_SECONDS 300, FM_EXPORT_RESERVE_SECONDS 120, FM_HEARTBEAT_TIMEOUT_SECONDS 120
"""
import hashlib
import json
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from .auth import FirebaseAuthenticator, IdentityToolkitRevocation, StaticInvites
from .catalog import MODELS, Catalog
from .credentials import (GoogleSecretVault, Secret, StreamResponse, UrllibTransport, VaultError,
                          adc_token_provider)
from .drive import (DriveAuthorization, DriveConfig, DriveController, DriveError, DriveUnavailable,
                    IntegrityError, MAX_ARTIFACT_BYTES)
from .lifecycle import Lifecycle
from .provider import ProviderError, RunPod
from .service import HostedError, HostedService

BASE = "/api/hosted/v1"
CALLBACK_PATH = BASE + "/connections/drive/callback"
PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
EMAIL = re.compile(r"^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9.-]{1,255}$")
OWNER = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
IMAGE = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")
COOKIE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ARTIFACT_ID = re.compile(r"^[a-f0-9]{32}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
DRIVE_UPLOAD_PREFIX = "https://www.googleapis.com/upload/drive/v3/files"
OIDC_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
BODY_LIMIT = 65536


class ConfigError(ValueError):
    """Invalid deployment configuration. Mentions variable names only, never values."""


# --------------------------------------------------------------------------- configuration
class _Reader:
    def __init__(self, env):
        self.env, self.problems = env, []

    def _bad(self, name, why):
        self.problems.append("%s: %s" % (name, why))

    def text(self, name, *, pattern=None, default=None, limit=512):
        value = (self.env.get(name) or "").strip()
        if not value:
            if default is not None:
                return default
            self._bad(name, "required")
            return ""
        if len(value) > limit or (pattern is not None and not pattern.fullmatch(value)):
            self._bad(name, "invalid")
            return ""
        return value

    def choice(self, name, options):
        value = self.text(name)
        if value and value not in options:
            self._bad(name, "must be one of " + "/".join(options))
            return ""
        return value

    def number(self, name, default, low, high, integer=True):
        raw = (self.env.get(name) or "").strip()
        if not raw:
            if default is None:
                self._bad(name, "required")
                return 0
            return default
        try:
            value = int(raw) if integer else float(raw)
        except ValueError:
            self._bad(name, "must be a number")
            return 0
        if not low <= value <= high or value != value:
            self._bad(name, "out of range")
            return 0
        return value

    def url(self, name, *, origin_only=True):
        raw = self.text(name, limit=2048)
        if not raw:
            return ""
        p = urllib.parse.urlsplit(raw)
        try:
            p.port
        except ValueError:
            self._bad(name, "invalid")
            return ""
        if (p.scheme != "https" or not p.hostname or p.username or p.password or p.query or p.fragment
                or (origin_only and p.path not in ("", "/"))):
            self._bad(name, "must be an https %s without credentials, query or fragment" % ("origin" if origin_only else "URL"))
            return ""
        return "https://" + p.netloc.lower() + ("" if origin_only else p.path)

    def done(self):
        if self.problems:
            raise ConfigError("Invalid hosted configuration: " + "; ".join(self.problems))


def _origin(url):
    p = urllib.parse.urlsplit(url)
    return p.scheme + "://" + p.netloc


def _drive_secret(reader):
    env = reader.env
    value, path = (env.get("FM_DRIVE_CLIENT_SECRET") or "").strip(), (env.get("FM_DRIVE_CLIENT_SECRET_FILE") or "").strip()
    if bool(value) == bool(path):
        reader._bad("FM_DRIVE_CLIENT_SECRET", "set exactly one of FM_DRIVE_CLIENT_SECRET or FM_DRIVE_CLIENT_SECRET_FILE")
        return None
    if value:
        return Secret(value)
    file = Path(path)
    if not file.is_absolute() or not file.is_file() or file.stat().st_size > 4096:
        reader._bad("FM_DRIVE_CLIENT_SECRET_FILE", "must be an absolute path to a small readable file")
        return None
    # Re-read per use so secret rotation by mounting a new version needs no restart.
    return lambda: Secret(file.read_text().strip())


@dataclass(frozen=True)
class _Common:
    environment: str
    project: str
    firestore_database: str
    firestore_namespace: str
    public_url: str
    dashboard_origin: str
    oidc_audience: str
    drive_client_id: str
    drive_redirect_uri: str
    drive_client_secret: Any = field(repr=False)

    @staticmethod
    def read(reader):
        values = dict(
            environment=reader.choice("FM_HOSTED_ENV", ("production", "staging")),
            project=reader.text("FM_GCP_PROJECT", pattern=PROJECT),
            firestore_database=reader.text("FM_FIRESTORE_DATABASE", default="(default)", limit=63),
            firestore_namespace=reader.text("FM_FIRESTORE_NAMESPACE", pattern=re.compile(r"^[a-z][a-z0-9_]{1,40}$"), default="hosted_v1"),
            public_url=reader.url("FM_PUBLIC_URL"),
            dashboard_origin=reader.url("FM_DASHBOARD_ORIGIN"),
            oidc_audience=reader.text("FM_CONTROLLER_OIDC_AUDIENCE", limit=2048),
            drive_client_id=reader.text("FM_DRIVE_CLIENT_ID", limit=256),
            drive_redirect_uri=reader.url("FM_DRIVE_REDIRECT_URI", origin_only=False),
            drive_client_secret=_drive_secret(reader))
        if values["public_url"] and values["drive_redirect_uri"] != values["public_url"] + CALLBACK_PATH:
            reader._bad("FM_DRIVE_REDIRECT_URI", "must equal FM_PUBLIC_URL + " + CALLBACK_PATH)
        return values

    def drive_config(self):
        return DriveConfig(self.drive_client_id, self.drive_redirect_uri)


@dataclass(frozen=True)
class PublicConfig(_Common):
    firebase_project: str = ""
    invited_emails: tuple = ()
    controller_url: str = ""
    post_auth_url: str = ""
    cookie_name: str = "__Host-fm_drive_oauth"

    def __repr__(self):
        return "PublicConfig(project=%s)" % self.project

    @classmethod
    def from_env(cls, env):
        r = _Reader(env)
        common = _Common.read(r)
        invited = tuple(sorted({e.lower() for e in re.split(r"[\s,;]+", env.get("FM_INVITED_EMAILS") or "") if e}))
        if not invited or len(invited) > 500 or not all(EMAIL.fullmatch(e) for e in invited):
            r._bad("FM_INVITED_EMAILS", "1-500 valid email addresses required")
        post = r.url("FM_DRIVE_POST_AUTH_URL", origin_only=False)
        if post and _origin(post) != common["dashboard_origin"]:
            r._bad("FM_DRIVE_POST_AUTH_URL", "must be on FM_DASHBOARD_ORIGIN")
        extra = dict(firebase_project=r.text("FM_FIREBASE_PROJECT_ID", pattern=PROJECT), invited_emails=invited,
                     controller_url=r.url("FM_CONTROLLER_URL"), post_auth_url=post,
                     cookie_name=r.text("FM_OAUTH_COOKIE_NAME", pattern=COOKIE, default="__Host-fm_drive_oauth"))
        r.done()
        return cls(**common, **extra)


@dataclass(frozen=True)
class ControllerConfig(_Common):
    public_service_account: str = ""
    scheduler_service_account: str = ""
    worker_image: str = ""
    disk_usd_per_gb_hour: float = 0.0
    disk_rate_source: str = ""
    disk_gb: int = 30
    maximum_seconds: int = 3600
    quote_ttl_seconds: int = 120
    startup_allowance_seconds: int = 300
    export_reserve_seconds: int = 120
    heartbeat_timeout_seconds: int = 120

    def __repr__(self):
        return "ControllerConfig(project=%s)" % self.project

    @classmethod
    def from_env(cls, env):
        r = _Reader(env)
        common = _Common.read(r)
        sa = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+\.iam\.gserviceaccount\.com$")
        extra = dict(
            public_service_account=r.text("FM_PUBLIC_SERVICE_ACCOUNT_EMAIL", pattern=sa).lower(),
            scheduler_service_account=r.text("FM_SCHEDULER_SERVICE_ACCOUNT_EMAIL", pattern=sa).lower(),
            worker_image=r.text("FM_WORKER_IMAGE", pattern=IMAGE),
            disk_usd_per_gb_hour=r.number("FM_DISK_USD_PER_GB_HOUR", None, 0.0, 10.0, integer=False),
            disk_rate_source=r.text("FM_DISK_RATE_SOURCE", limit=200),
            disk_gb=r.number("FM_DISK_GB", 30, 1, 500),
            maximum_seconds=r.number("FM_MAX_SESSION_SECONDS", 3600, 600, 86400),
            quote_ttl_seconds=r.number("FM_QUOTE_TTL_SECONDS", 120, 30, 600),
            startup_allowance_seconds=r.number("FM_STARTUP_ALLOWANCE_SECONDS", 300, 60, 1800),
            export_reserve_seconds=r.number("FM_EXPORT_RESERVE_SECONDS", 120, 30, 1800),
            heartbeat_timeout_seconds=r.number("FM_HEARTBEAT_TIMEOUT_SECONDS", 120, 30, 900))
        if extra["public_service_account"] and extra["public_service_account"] == extra["scheduler_service_account"]:
            r._bad("FM_SCHEDULER_SERVICE_ACCOUNT_EMAIL", "must differ from the public service account")
        if extra["startup_allowance_seconds"] + extra["export_reserve_seconds"] >= extra["maximum_seconds"]:
            r._bad("FM_MAX_SESSION_SECONDS", "must exceed startup allowance plus export reserve")
        r.done()
        return cls(**common, **extra)


# --------------------------------------------------------------------------- shared helpers
def _digest(*parts):
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


class WriteOnlyVault:
    """Public identity view of the vault: it may store and delete, never read."""
    durable = True

    def __init__(self, inner):
        self._inner = inner

    def put(self, owner, kind, value):
        return self._inner.put(owner, kind, value)

    def delete(self, ref):
        return self._inner.delete(ref)

    def get(self, ref, *, owner=None):
        raise VaultError("This identity cannot read secrets.")

    def __repr__(self):
        return "WriteOnlyVault()"


async def _read_json(request, limit=BODY_LIMIT):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > limit:
            raise HostedError(413, "Request too large")
    try:
        value = json.loads(raw or b"{}")
    except ValueError:
        raise HostedError(422, "Invalid JSON") from None
    if not isinstance(value, dict):
        raise HostedError(422, "JSON object required")
    return value


def _register_errors(app):
    from fastapi.responses import JSONResponse

    @app.exception_handler(HostedError)
    async def hosted(request, exc):
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

    @app.exception_handler(Exception)
    async def unavailable(request, exc):
        return JSONResponse(status_code=503, content={"detail": "Hosted integration unavailable"})


# --------------------------------------------------------------------------- internal OIDC (controller side)
def _google_verify(token, audience):
    from google.auth.transport import requests as grequests  # lazy optional dependency
    from google.oauth2 import id_token
    return id_token.verify_oauth2_token(token, grequests.Request(), audience)


class OidcVerifier:
    """Verifies a Google-signed ID token for the exact audience and returns the caller email."""

    def __init__(self, audience, verify=None):
        self.audience, self._verify = audience, verify or _google_verify

    def email(self, token):
        try:
            claims = self._verify(token, self.audience)
        except Exception:
            raise HostedError(401, "Service authentication failed") from None
        email = claims.get("email") if isinstance(claims, dict) else None
        if (claims.get("aud") != self.audience or claims.get("iss") not in OIDC_ISSUERS
                or claims.get("email_verified") is not True or not isinstance(email, str)):
            raise HostedError(401, "Service authentication failed")
        return email.lower()


# --------------------------------------------------------------------------- public -> controller client
def _google_id_token_provider(audience, clock=time.time):
    cache = {"exp": 0.0}

    def provide():
        now = clock()
        if cache["exp"] <= now:
            from google.auth.transport import requests as grequests  # lazy optional dependency
            from google.oauth2 import id_token
            cache.update(token=id_token.fetch_id_token(grequests.Request(), audience), exp=now + 45 * 60)
        return cache["token"]
    return provide


class ControllerClient:
    """Bounded, authenticated calls from the public API to the private controller."""

    def __init__(self, base_url, token_provider, transport):
        self.base, self._token, self.http = base_url.rstrip("/"), token_provider, transport

    def _headers(self):
        try:
            token = self._token()
        except Exception:
            raise HostedError(503, "Hosted integration unavailable") from None
        return {"Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json"}

    @staticmethod
    def _failure(status, body):
        """Pass through only the controller's own short client-error messages."""
        if status in (400, 404, 409, 422):
            try:
                detail = json.loads(body.decode()).get("detail")
            except (ValueError, AttributeError):
                detail = None
            return HostedError(status, detail[:200] if isinstance(detail, str) else "Request rejected")
        return HostedError(503, "Hosted integration unavailable")

    def call(self, path, payload, *, timeout=15.0):
        try:
            response = self.http.request("POST", self.base + "/internal/" + path, self._headers(),
                                         json.dumps(payload, allow_nan=False).encode(), timeout)
        except HostedError:
            raise
        except Exception:
            raise HostedError(503, "Hosted integration unavailable") from None
        if response.status != 200:
            raise self._failure(response.status, response.body)
        try:
            value = response.json()
        except Exception:
            value = None
        if not isinstance(value, dict):
            raise HostedError(503, "Hosted integration unavailable")
        return value

    def stream(self, path, payload, *, timeout=60.0):
        """Returns (headers, chunk iterator); the stream must end at exactly Content-Length."""
        try:
            response = self.http.stream("POST", self.base + "/internal/" + path, self._headers(),
                                        json.dumps(payload, allow_nan=False).encode(), timeout)
        except HostedError:
            raise
        except Exception:
            raise HostedError(503, "Hosted integration unavailable") from None
        if response.status != 200:
            try:
                body = b"".join(response.chunks)[:BODY_LIMIT]
            except Exception:
                body = b""
            finally:
                response.close()
            raise self._failure(response.status, body)
        length = response.header("Content-Length")
        if not (length or "").isdigit() or not 0 < int(length) <= MAX_ARTIFACT_BYTES:
            response.close()
            raise HostedError(503, "Hosted integration unavailable")
        expected = int(length)
        headers = {k: response.header(k) for k in ("Content-Type", "Content-Length", "Content-Disposition") if response.header(k)}

        def relay():
            sent = 0
            try:
                for chunk in response.chunks:
                    sent += len(chunk)
                    if sent > expected:
                        raise RuntimeError("Controller stream exceeded its declared length.")
                    yield chunk
                if sent != expected:
                    raise RuntimeError("Controller stream ended early.")
            finally:
                response.close()
        return headers, relay()


class RemoteCatalog:
    """Public-side catalog: models are static pins; quotes are priced by the controller."""

    def __init__(self, client):
        self.client = client

    def models(self):
        return [dict(model) for model in MODELS]

    def quote(self, body, now):
        request = {"owner_uid": body.get("owner_uid")}
        for key in ("model_id", "max_duration_seconds", "gpu_id", "max_usd"):
            if key in body:
                request[key] = body[key]
        quote = self.client.call("catalog/quote", request, timeout=40.0)
        pin = next((m for m in MODELS if m["id"] == quote.get("model_id")), None)
        if pin is None or quote.get("model_revision") != pin["revision"]:
            raise HostedError(503, "Hosted integration unavailable")
        return quote


# --------------------------------------------------------------------------- controller internals
class RunPodNotConnected(ProviderError):
    pass


def make_provider_factory(store, vault, runpod=RunPod):
    """Resolve a RunPod client for a session (its own ref first) or the owner's current connection."""

    def factory(subject):
        owner = subject.get("owner_uid")
        if not isinstance(owner, str) or not OWNER.fullmatch(owner):
            raise RunPodNotConnected()
        refs = [subject["credential_ref"]] if subject.get("credential_ref") else []
        connection = store.transaction(lambda tx: tx.get("connections", owner + "_runpod")) or {}
        if connection.get("connected") and connection.get("credential_ref") and connection["credential_ref"] not in refs:
            refs.append(connection["credential_ref"])
        for ref in refs:
            try:
                return runpod(vault.get(ref, owner=owner))
            except VaultError:
                continue
        raise RunPodNotConnected()
    return factory


def _drive_failure(exc):
    status = 503 if isinstance(exc, DriveUnavailable) else 409 if isinstance(exc, IntegrityError) else exc.status
    return HostedError(status, exc.public_message if status < 500 else "Storage temporarily unavailable")


def _artifact(value, *, extra=()):
    allowed = {"id", "sha256", "size_bytes", *extra}
    if (not isinstance(value, dict) or not {"id", "sha256", "size_bytes"} <= set(value) or not set(value) <= allowed
            or not isinstance(value["id"], str) or not ARTIFACT_ID.fullmatch(value["id"])
            or not isinstance(value["sha256"], str) or not SHA.fullmatch(value["sha256"])
            or isinstance(value["size_bytes"], bool) or not isinstance(value["size_bytes"], int)
            or not 0 < value["size_bytes"] <= MAX_ARTIFACT_BYTES):
        raise HostedError(422, "Invalid artifact metadata")
    return {"id": value["id"], "sha256": value["sha256"], "size_bytes": value["size_bytes"]}


class DriveArtifacts:
    """Controller-held Drive operations for worker evidence uploads."""

    def __init__(self, store, drive):
        self.store, self.drive = store, drive

    def _context(self, owner, session_id, job_id):
        session, job = self.store.transaction(lambda tx: (tx.get("sessions", session_id), tx.get("jobs", job_id)))
        if (not session or session.get("owner_uid") != owner or session.get("storage_mode") != "drive"
                or not job or job.get("owner_uid") != owner or job.get("session_id") != session_id):
            raise HostedError(404, "Resource not found")

    def prepare(self, owner, session_id, job_id, artifact):
        self._context(owner, session_id, job_id)
        art = _artifact(artifact)
        key = _digest(owner, job_id, art["id"])
        try:
            record = self.store.transaction(lambda tx: tx.get("drive_prepares", key))
            if record is None:
                ref = self.drive.register_file(owner, job_id, "fm-evidence-%s.json" % art["id"], art["size_bytes"], art["sha256"])["file_ref"]

                def claim(tx):
                    old = tx.get("drive_prepares", key)
                    if old:
                        return old
                    new = {"owner_uid": owner, "job_id": job_id, "artifact_id": art["id"], "file_ref": ref}
                    tx.set("drive_prepares", key, new)
                    return new
                record = self.store.transaction(claim)
            f = self.drive._file(owner, record["file_ref"])
            if (f["size"], f["sha256"]) != (art["size_bytes"], art["sha256"]):
                raise HostedError(409, "Artifact is immutable")
            if f["status"] in ("registered", "creating"):
                capability = self.drive.create_upload_session(owner, record["file_ref"])
                url = capability.url.reveal() if isinstance(capability.url, Secret) else capability.url
                expires = capability.expires_at
            elif f["status"] in ("session", "uploaded"):
                url, expires = self.drive._session_url(owner, f), f.get("session_expires_at")
            else:
                raise HostedError(409, "Artifact upload is not available")
        except DriveError as exc:
            raise _drive_failure(exc) from None
        return {"file_ref": record["file_ref"], "upload_url": url, "expires_at": expires}

    def verify(self, owner, session_id, job_id, artifacts):
        if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 32:
            raise HostedError(422, "Invalid artifact metadata")
        self._context(owner, session_id, job_id)
        verified = []
        for item in artifacts:
            art = _artifact(item, extra=("file_ref", "destination"))
            record = self.store.transaction(lambda tx: tx.get("drive_prepares", _digest(owner, job_id, art["id"])))
            if record is None:
                raise HostedError(409, "Artifact upload was not prepared")
            try:
                confirmed = self.drive.confirm(owner, record["file_ref"])
            except DriveError as exc:
                raise _drive_failure(exc) from None
            if confirmed["sha256"] != art["sha256"] or confirmed["size"] != art["size_bytes"]:
                raise HostedError(409, "Artifact does not match the verified upload")
            verified.append(dict(art, file_ref=record["file_ref"]))
        return {"artifacts": verified}


@dataclass
class ControllerDeps:
    store: Any
    vault: Any
    verifier: OidcVerifier
    runpod: Callable = RunPod
    drive_transport: Any = None
    clock: Callable = time.time


def assemble_controller_app(config, deps):
    from fastapi import FastAPI, Request
    from fastapi.responses import StreamingResponse
    from starlette.concurrency import run_in_threadpool

    clock = deps.clock
    provider_factory = make_provider_factory(deps.store, deps.vault, deps.runpod)
    catalog = Catalog(provider_factory, disk_usd_per_gb_hour=config.disk_usd_per_gb_hour,
                      disk_rate_source=config.disk_rate_source, disk_gb=config.disk_gb,
                      maximum_seconds=config.maximum_seconds, quote_ttl_seconds=config.quote_ttl_seconds,
                      startup_allowance_seconds=config.startup_allowance_seconds,
                      export_reserve_seconds=config.export_reserve_seconds)
    service = HostedService(deps.store, clock=clock, vault=deps.vault, catalog=catalog, lifecycle=None)
    # The lifecycle needs the service's claim/enrollment methods, so it is attached afterwards.
    service.lifecycle = Lifecycle(
        provider_factory, worker_image=config.worker_image, control_plane_url=config.public_url,
        persist_before_create=service.persist_before_create,
        worker_environment=lambda session: {"FM_ENROLLMENT_TOKEN": service.prepare_enrollment(session["id"])},
        clock=clock, dashboard_origin=config.dashboard_origin,
        heartbeat_timeout_seconds=config.heartbeat_timeout_seconds)
    drive = DriveController(deps.store, deps.vault, config.drive_config(), config.drive_client_secret,
                            deps.drive_transport, clock)
    artifacts = DriveArtifacts(deps.store, drive)

    app = FastAPI(title="Fork Microscope hosted controller", openapi_url=None, docs_url=None, redoc_url=None)
    _register_errors(app)

    def owner_of(payload, *, keys):
        if not set(payload) <= set(keys) | {"owner_uid"}:
            raise HostedError(422, "Invalid request")
        owner = payload.get("owner_uid")
        if not isinstance(owner, str) or not OWNER.fullmatch(owner):
            raise HostedError(422, "Invalid request")
        return owner

    def quote(payload):
        owner = owner_of(payload, keys=("model_id", "max_duration_seconds", "gpu_id", "max_usd"))
        try:
            return catalog.quote(dict(payload, owner_uid=owner), clock())
        except RunPodNotConnected:
            raise HostedError(409, "Connect RunPod first") from None
        except ProviderError:
            raise HostedError(503, "Compute provider unavailable") from None
        except ValueError as exc:
            raise HostedError(422, str(exc)[:200]) from None

    def drive_status(payload):
        return drive.status(owner_of(payload, keys=()))

    def drive_disconnect(payload):
        return drive.disconnect(owner_of(payload, keys=()))

    def drive_prepare(payload):
        owner = owner_of(payload, keys=("session_id", "job_id", "artifact"))
        return artifacts.prepare(owner, _id(payload, "session_id"), _id(payload, "job_id"), payload.get("artifact"))

    def drive_verify(payload):
        owner = owner_of(payload, keys=("session_id", "job_id", "artifacts"))
        return artifacts.verify(owner, _id(payload, "session_id"), _id(payload, "job_id"), payload.get("artifacts"))

    def drive_download(payload):
        owner = owner_of(payload, keys=("file_ref",))
        ref = payload.get("file_ref")
        if not isinstance(ref, str) or not re.fullmatch(r"df_[0-9a-f]{32}", ref):
            raise HostedError(422, "Invalid request")
        try:
            return drive.open_download(owner, ref)
        except DriveError as exc:
            raise _drive_failure(exc) from None

    def guarded(handler):
        def run(payload):
            try:
                return handler(payload)
            except DriveError as exc:
                raise _drive_failure(exc) from None
        return run

    def route(path, who, handler, *, streaming=False):
        async def endpoint(request: Request):
            auth = request.headers.get("authorization", "")
            if not auth.startswith("Bearer ") or len(auth) > 8192:
                raise HostedError(401, "Service authentication required")
            email = await run_in_threadpool(deps.verifier.email, auth[7:])
            if email != who:
                raise HostedError(403, "Service identity not permitted")
            result = await run_in_threadpool(handler, await _read_json(request))
            if streaming:
                headers, chunks = result
                return StreamingResponse(chunks, headers=headers)
            return result
        app.add_api_route("/internal/" + path, endpoint, methods=["POST"])

    publisher = config.public_service_account
    route("reconcile", config.scheduler_service_account, lambda payload: service.reconcile())
    route("catalog/quote", publisher, quote)
    route("drive/status", publisher, guarded(drive_status))
    route("drive/disconnect", publisher, guarded(drive_disconnect))
    route("drive/artifact-prepare", publisher, drive_prepare)
    route("drive/artifact-verify", publisher, drive_verify)
    route("drive/download", publisher, drive_download, streaming=True)
    app.state.service = service
    return app


def _id(payload, name):
    value = payload.get(name)
    if not isinstance(value, str) or not OWNER.fullmatch(value):
        raise HostedError(422, "Invalid request")
    return value


# --------------------------------------------------------------------------- public API
@dataclass
class PublicDeps:
    store: Any
    authenticator: Any
    vault: Any
    controller: ControllerClient
    drive_transport: Any = None
    clock: Callable = time.time
    allow_process_local: bool = False  # simulations only; OAuth state must be durable in production


def assemble_public_app(config, deps):
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
    from starlette.concurrency import run_in_threadpool
    from .api import create_app

    client, vault = deps.controller, WriteOnlyVault(deps.vault)
    drive_auth = DriveAuthorization(deps.store, vault, config.drive_config(), config.drive_client_secret,
                                    deps.drive_transport, deps.clock, deps.allow_process_local)

    def artifact_prepare(session, job, body):
        if session.get("storage_mode") != "drive":
            raise HostedError(409, "Session does not use Drive storage")
        grant = client.call("drive/artifact-prepare", {"owner_uid": session["owner_uid"], "session_id": session["id"],
                                                       "job_id": job["id"], "artifact": body.get("artifact")})
        url = grant.get("upload_url")
        if not isinstance(grant.get("file_ref"), str) or not isinstance(url, str) or not url.startswith(DRIVE_UPLOAD_PREFIX):
            raise HostedError(503, "Hosted integration unavailable")
        return {"file_ref": grant["file_ref"], "upload_url": url, "expires_at": grant.get("expires_at")}

    def artifact_verify(session, job, artifacts):
        wanted = [{k: a.get(k) for k in ("id", "sha256", "size_bytes") if k in a} for a in artifacts]
        verified = client.call("drive/artifact-verify", {"owner_uid": session["owner_uid"], "session_id": session["id"],
                                                         "job_id": job["id"], "artifacts": wanted}, timeout=60.0)["artifacts"]
        if not isinstance(verified, list) or len(verified) != len(artifacts):
            raise HostedError(503, "Hosted integration unavailable")
        # Core stores whatever the report list holds afterwards, so normalise it to the verified
        # opaque reference. Drive ids reported by workers are never persisted.
        for given, good in zip(artifacts, verified):
            if given.get("id") != good.get("id") or not isinstance(good.get("file_ref"), str):
                raise HostedError(503, "Hosted integration unavailable")
            given["file_ref"], given["size_bytes"] = good["file_ref"], good["size_bytes"]

    service = HostedService(deps.store, clock=deps.clock, vault=vault, catalog=RemoteCatalog(client), lifecycle=None,
                            artifact_prepare=artifact_prepare, artifact_verify=artifact_verify)
    app = create_app(service, deps.authenticator, worker_enabled=True, controller_enabled=False,
                     allowed_origins=[config.dashboard_origin])
    _register_errors(app)  # never leak exception detail from routes added here
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") not in
                            ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc")]

    async def identity(request):
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            raise HostedError(401, "Bearer authentication required")
        try:
            uid = (await run_in_threadpool(deps.authenticator.verify, auth[7:])).get("uid")
        except Exception:
            raise HostedError(401, "Authentication failed") from None
        if not isinstance(uid, str) or not OWNER.fullmatch(uid):
            raise HostedError(401, "Authentication failed")
        return uid

    quiet = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    router = APIRouter()

    @router.get(BASE + "/connections/drive")
    async def drive_status(request: Request):
        uid = await identity(request)
        return JSONResponse(await run_in_threadpool(client.call, "drive/status", {"owner_uid": uid}), headers=quiet)

    @router.post(BASE + "/connections/drive/disconnect")
    async def drive_disconnect(request: Request):
        uid = await identity(request)
        return JSONResponse(await run_in_threadpool(client.call, "drive/disconnect", {"owner_uid": uid}), headers=quiet)

    @router.post(BASE + "/connections/drive/begin")
    async def drive_begin(request: Request):
        uid = await identity(request)
        start = await run_in_threadpool(drive_auth.begin, uid)
        response = JSONResponse({"authorization_url": start.url}, headers=quiet)
        response.set_cookie(config.cookie_name, start.binding, max_age=600, path="/", secure=True, httponly=True, samesite="lax")
        return response

    @router.get(CALLBACK_PATH)
    async def drive_callback(request: Request):
        # Reached by a browser redirect from Google: identity is the owner-bound, single-use
        # state plus the HttpOnly cookie set by /begin. The destination never depends on input.
        query, ok = request.query_params, False
        binding = request.cookies.get(config.cookie_name)
        if "error" not in query and binding:
            try:
                await run_in_threadpool(drive_auth.complete, query.get("state", ""), query.get("code", ""), binding)
                ok = True
            except Exception:
                ok = False
        response = RedirectResponse(config.post_auth_url + ("?drive=connected" if ok else "?drive=error"),
                                    status_code=303, headers=quiet)
        response.delete_cookie(config.cookie_name, path="/", secure=True, httponly=True, samesite="lax")
        return response

    @router.get(BASE + "/artifacts/{artifact_id}/content")
    async def artifact_content(request: Request, artifact_id: str):
        uid = await identity(request)
        # Owner check and Drive-vs-device decision live in core; Drive bytes outlive the pod.
        artifact = await run_in_threadpool(service.public, uid, "download", {}, artifact_id, None)
        if "url" in artifact or not isinstance(artifact.get("file_ref"), str):
            raise HostedError(409, "Artifact is stored on your device")
        headers, chunks = await run_in_threadpool(client.stream, "drive/download", {"owner_uid": uid, "file_ref": artifact["file_ref"]})
        return StreamingResponse(chunks, headers=dict(headers, **{"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}))

    app.router.routes[:0] = router.routes  # take precedence over core's placeholder drive status
    app.state.service = service
    return app


# --------------------------------------------------------------------------- production factories
def _firestore(config):
    from google.cloud import firestore  # lazy optional dependency
    from .store import FirestoreStore
    return FirestoreStore(firestore.Client(project=config.project, database=config.firestore_database),
                          config.firestore_namespace)


def create_public_app(environ=None):
    import os
    config = PublicConfig.from_env(os.environ if environ is None else environ)
    authenticator = FirebaseAuthenticator(
        config.firebase_project, StaticInvites(config.invited_emails),
        IdentityToolkitRevocation(config.firebase_project, adc_token_provider()))
    host = urllib.parse.urlsplit(config.controller_url).hostname
    client = ControllerClient(config.controller_url, _google_id_token_provider(config.oidc_audience),
                              UrllibTransport({host}, max_response_bytes=1024 * 1024))
    return assemble_public_app(config, PublicDeps(_firestore(config), authenticator,
                                                  GoogleSecretVault(config.project), client))


def create_controller_app(environ=None):
    import os
    config = ControllerConfig.from_env(os.environ if environ is None else environ)
    return assemble_controller_app(config, ControllerDeps(_firestore(config), GoogleSecretVault(config.project),
                                                          OidcVerifier(config.oidc_audience)))


# --------------------------------------------------------------------------- simulated end-to-end wiring
class InProcessTransport:
    """Transport that dispatches to an ASGI app (tests only); exercises the real HTTP routes."""

    def __init__(self, app):
        from starlette.testclient import TestClient  # lazy, test-only (needs httpx)
        self.client = TestClient(app, raise_server_exceptions=False)

    def request(self, method, url, headers=None, body=None, timeout=30.0):
        from .credentials import HttpResponse
        r = self.client.request(method, url, headers=headers, content=body)
        return HttpResponse(r.status_code, dict(r.headers), r.content)

    def stream(self, method, url, headers=None, body=None, timeout=60.0):
        cm = self.client.stream(method, url, headers=headers, content=body)
        r = cm.__enter__()
        return StreamResponse(r.status_code, dict(r.headers), r.iter_bytes(), lambda: cm.__exit__(None, None, None))


class OptimisticMemoryStore:
    """Firestore-like in-memory store for simulations: bodies run on a snapshot without holding a
    lock and commit only if nothing changed, else rerun. Unlike a global lock this lets a public
    transaction call the controller (which writes the same store) without deadlocking, and it
    exercises the same body-may-run-twice behaviour as Firestore transactions."""
    durable = False

    def __init__(self):
        import threading
        self._data, self._version, self._lock = {}, 0, threading.Lock()

    def transaction(self, fn):
        import copy
        for _ in range(25):
            with self._lock:
                data, version = copy.deepcopy(self._data), self._version
            tx = _SnapshotTransaction(data)
            result = fn(tx)
            with self._lock:
                if not tx.dirty:
                    return copy.deepcopy(result)
                if version == self._version:
                    self._data, self._version = data, version + 1
                    return copy.deepcopy(result)
        raise HostedError(503, "Store contention")


class _SnapshotTransaction:
    def __init__(self, data):
        self.data, self.dirty = data, False

    def get(self, collection, key):
        import copy
        return copy.deepcopy(self.data.get(collection, {}).get(key))

    def set(self, collection, key, value):
        import copy
        self.data.setdefault(collection, {})[key] = copy.deepcopy(value)
        self.dirty = True

    def list(self, collection):
        import copy
        return copy.deepcopy(list(self.data.get(collection, {}).values()))


@dataclass
class SimulatedApps:
    __test__ = False
    public: Any
    controller: Any
    store: Any
    vault: Any
    public_config: PublicConfig
    controller_config: ControllerConfig
    tokens: Dict[str, str]

    def internal(self, who):
        return {"Authorization": "Bearer " + self.tokens[who]}


def sample_environment(**overrides):
    env = {
        "FM_HOSTED_ENV": "staging", "FM_GCP_PROJECT": "fm-test-project", "FM_FIREBASE_PROJECT_ID": "fm-test-project",
        "FM_PUBLIC_URL": "https://app.example.test", "FM_DASHBOARD_ORIGIN": "https://app.example.test",
        "FM_CONTROLLER_URL": "https://controller.example.test", "FM_CONTROLLER_OIDC_AUDIENCE": "https://controller.example.test",
        "FM_INVITED_EMAILS": "alice@example.test, bob@example.test",
        "FM_DRIVE_CLIENT_ID": "client.apps.googleusercontent.com", "FM_DRIVE_CLIENT_SECRET": "test-client-secret",
        "FM_DRIVE_REDIRECT_URI": "https://app.example.test" + CALLBACK_PATH,
        "FM_DRIVE_POST_AUTH_URL": "https://app.example.test/hosted.html",
        "FM_PUBLIC_SERVICE_ACCOUNT_EMAIL": "public@fm-test-project.iam.gserviceaccount.com",
        "FM_SCHEDULER_SERVICE_ACCOUNT_EMAIL": "scheduler@fm-test-project.iam.gserviceaccount.com",
        "FM_WORKER_IMAGE": "registry.example.test/fm-worker@sha256:" + "a" * 64,
        "FM_DISK_USD_PER_GB_HOUR": "0.0001", "FM_DISK_RATE_SOURCE": "test fixture"}
    env.update(overrides)
    return env


def create_test_apps(*, users, runpod, google_transport, vault=None, store=None, clock=time.time, env=None):
    """Wire both apps with explicit fakes (``users`` {token: {uid,email}}, a FakeRunPod, a Google
    transport stub). Internal calls travel over real HTTP routes in-process; Google ID token
    verification is replaced by a verifier accepting ``oidc:<email>`` tokens, nothing else."""
    from .auth import FakeAuthenticator
    from .credentials import InMemoryVault
    env = env or sample_environment()
    public_config, controller_config = PublicConfig.from_env(env), ControllerConfig.from_env(env)
    store, vault = store or OptimisticMemoryStore(), vault or InMemoryVault()

    def verify(token, audience):
        if not token.startswith("oidc:"):
            raise ValueError("bad token")
        return {"aud": audience, "iss": OIDC_ISSUERS[0], "email": token[5:], "email_verified": True}

    tokens = {"public": "oidc:" + controller_config.public_service_account,
              "scheduler": "oidc:" + controller_config.scheduler_service_account}
    controller = assemble_controller_app(controller_config, ControllerDeps(
        store, vault, OidcVerifier(controller_config.oidc_audience, verify), lambda key: runpod, google_transport, clock))
    client = ControllerClient(public_config.controller_url, lambda: tokens["public"], InProcessTransport(controller))
    public = assemble_public_app(public_config, PublicDeps(store, FakeAuthenticator(users), vault, client, google_transport, clock, True))
    return SimulatedApps(public, controller, store, vault, public_config, controller_config, tokens)
