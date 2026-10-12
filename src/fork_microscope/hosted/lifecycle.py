"""Controller reconciliation: durable create claim, absolute deadlines, verified deletion.

Invoke periodically from an independent controller; browser and worker heartbeats
are never required for cleanup. An uncertain create is reconciled by exact name,
never repeated. Missing pods after uncertain create remain watched indefinitely.
"""
import re
import time
from urllib.parse import urlsplit
from .provider import CreateUncertain, ProviderError
from .catalog import immutable_worker_image

# Controller retry states, not reasons a session was terminated.
TRANSIENT_ERRORS = {"create_outcome_pending", "create_outcome_manual_attention",
                    "provider_unavailable", "credential_unavailable"}


class Lifecycle:
    def __init__(self, provider_factory, *, worker_image, control_plane_url,
                 persist_before_create, worker_environment, clock=time.time, dashboard_origin=None,
                 heartbeat_timeout_seconds=120, uncertain_create_grace_seconds=300,
                 allow_unbound_quotes=False):
        immutable_worker_image(worker_image)
        if type(allow_unbound_quotes) is not bool:
            raise ValueError("allow_unbound_quotes must be a boolean")
        self.allow_unbound_quotes=allow_unbound_quotes
        url = urlsplit(control_plane_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Configured HTTPS control plane required")
        origin = urlsplit(dashboard_origin or control_plane_url)
        if origin.scheme != "https" or not origin.hostname or origin.username or origin.password or origin.query or origin.fragment or origin.path not in {"", "/"}:
            raise ValueError("Configured HTTPS dashboard origin required")
        self.dashboard_origin = f"https://{origin.netloc}"
        if type(uncertain_create_grace_seconds) is not int or uncertain_create_grace_seconds < 0:
            raise ValueError("Invalid uncertain create grace")
        self.uncertain_create_grace = uncertain_create_grace_seconds
        self.heartbeat_timeout = heartbeat_timeout_seconds
        self.provider_factory = provider_factory
        self.image = worker_image
        self.url = control_plane_url.rstrip("/")
        self.claim = persist_before_create
        self.environment = worker_environment
        self.clock = clock

    def reconcile_session(self, session):
        now = self.clock()
        terminate = session.get("desired_state") == "terminated" or now >= session["expires_at"]
        deadline_code = None
        state = session.get("observed_state")
        created = session.get("created_at")
        if created is not None and state in {"requested", "provisioning", "booting"} and now >= created + session["quote"]["startup_allowance_seconds"]:
            terminate, deadline_code = True, "startup_timeout"
        heartbeat = session.get("worker_last_heartbeat_at", session.get("heartbeat_at"))
        if heartbeat is not None and state in {"ready", "running", "saving"} and now >= heartbeat + self.heartbeat_timeout:
            terminate, deadline_code = True, "worker_heartbeat_timeout"
        if session.get('observed_state') == 'ready' and not session.get('has_started_job') and now >= session.get('idle_since', now) + 300:
            terminate = True
        name = session["request_name"]
        if not re.fullmatch(r"fm-[a-zA-Z0-9_-]{8,120}", name):
            raise ValueError("Invalid managed request name")
        image=session['quote'].get('worker_image')
        # Reject only creation admission. Already attempted creates and existing
        # pods remain observed/cleaned by exact name even after controller rollout;
        # an image setting change must never strand their resource fence.
        if not terminate and not session.get('create_attempted') and not session.get('provider_ref'):
            try:
                if 'worker_image' not in session['quote'] and self.allow_unbound_quotes:
                    image=self.image
                if immutable_worker_image(image)!=self.image:
                    raise ValueError('Reviewed worker image changed')
            except ValueError:
                return {'desired_state':'terminated','observed_state':'terminating','cleanup_verified':False,
                        'error_code':'worker_image_review_required'}
        try:
            provider = self.provider_factory(session)
            if provider is None:
                raise ValueError("Controller credential unavailable")
        except Exception:
            # Credential lookup can fail independently of the provider API.
            # Keep the owner resource fence and let the controller retry cleanup.
            return {"desired_state": "terminated" if terminate else session.get("desired_state", "running"),
                    "observed_state": "terminating" if terminate else session.get("observed_state", "provisioning"),
                    "cleanup_verified": False, "cleanup_status": "credential_blocked",
                    "error_code": "credential_unavailable", "manual_attention_required": True}
        try:
            pods = [pod for pod in provider.list_pods() if pod.get("name") == name]
            ref = session.get("provider_ref")
            mismatch = False
            if ref:
                referenced = provider.get_pod(ref)
                if referenced and referenced.get("name") != name:
                    # Never delete a differently named pod, but still terminate and
                    # clean exact-name pods. The unexplained reference blocks verified
                    # cleanup until an operator confirms its absence.
                    referenced, mismatch, terminate = None, True, True
                    deadline_code = "provider_ownership_mismatch"
                if referenced and not any(p["id"] == ref for p in pods):
                    pods.append(referenced)
            if terminate:
                for pod in pods:
                    provider.delete_pod(pod["id"])
                remaining = [p for p in provider.list_pods() if p.get("name") == name]
                remaining.extend(p for p in (provider.get_pod(pod["id"]) for pod in pods) if p)
                # If a POST crashed, absence is not proof it will never appear.
                uncertain = session.get("create_attempted") and not session.get("create_rejected") and not session.get("provider_ref") and not pods
                # A later cleanup tick has no deadline of its own; keep the recorded
                # termination cause rather than erasing it, but drop transient codes.
                prior = session.get("error_code")
                cause = deadline_code or (None if prior in TRANSIENT_ERRORS else prior)
                attention = mismatch or bool(uncertain and now >= session["expires_at"] + self.uncertain_create_grace)
                blocked = bool(remaining or uncertain or mismatch)
                return {"desired_state": "terminated", "observed_state": "terminating" if blocked else "terminated",
                        "cleanup_verified_at": None if blocked else now,
                        "cleanup_verified": not blocked,
                        "uncertain_resource_fence": bool(uncertain),
                        "manual_attention_required": attention,
                        "cleanup_status": "manual_attention" if attention else "pending" if blocked else "verified",
                        **({"provider_ref": pods[0]["id"]} if pods and not mismatch else {}),
                        "error_code": deadline_code if mismatch else "create_outcome_manual_attention" if attention else cause}
            if len(pods) > 1:
                # All share this session's exact managed name. Delete on next tick.
                return {"desired_state": "terminated", "observed_state": "terminating", "error_code": "duplicate_provider_name"}
            if pods:
                pod = pods[0]
                status = pod.get("status")
                if status in {"FAILED", "EXITED", "STOPPED", "TERMINATED"}:
                    return {"provider_ref": pod["id"], "desired_state": "terminated", "observed_state": "terminating", "error_code": "worker_stopped"}
                # Provider RUNNING is not worker readiness. Worker enrollment owns ready.
                state = session.get("observed_state")
                return {"provider_ref": pod["id"], "observed_state": state if state in {"ready", "running", "saving"} else "booting"}
            if session.get("create_attempted") or ref:
                # A persisted provider reference itself proves creation already
                # happened, including legacy records without the explicit flag.
                # Observation after restart must never admit a replacement POST.
                return {"observed_state": "provisioning", "error_code": "create_outcome_pending"}
            quote = session["quote"]
            # Quote freshness was approved at session acceptance, not at this
            # asynchronous controller tick. Startup and absolute deadlines still apply.
            if session.get("created_at", now) >= quote["expires_at"]:
                return {"desired_state": "terminated", "observed_state": "failed", "error_code": "stale_quote"}
            env = self.environment(session)
            if any("RUNPOD" in key.upper() or "DRIVE" in key.upper() for key in env):
                raise ValueError("Provider and Drive credentials forbidden in worker environment")
            if not self.claim(session["id"], {"create_attempted": True, "observed_state": "provisioning"}):
                return {"observed_state": "provisioning"}
            body = {"name": name, "image": image,
                    "gpu": {"id": quote["gpu_id"], "count": 1, "minCudaVersion": "12.8"},
                    "cloud": "SECURE", "disk": quote["disk_gb"], "ports": ["8780/http"] if session.get("storage_mode", session.get("destination")) == "device" else [], "startSsh": False,
                    "startJupyter": False, "env": dict(env, FM_CONTROL_PLANE_URL=self.url,
                        FM_DASHBOARD_ORIGIN=self.dashboard_origin,
                        FM_SESSION_ID=session["id"], FM_EXPIRES_AT=str(session["expires_at"]),
                        FM_MODEL_ID=quote["model_id"], FM_MODEL_REVISION=quote["model_revision"])}
            try:
                pod = provider.create_pod(body)
            except CreateUncertain:
                return {"observed_state": "provisioning", "error_code": "create_outcome_pending"}
            except ProviderError:
                return {"desired_state": "terminated", "observed_state": "terminating", "error_code": "create_rejected", "create_rejected": True}
            return {"provider_ref": pod["id"], "observed_state": "booting"}
        except ProviderError:
            return {"observed_state": "terminating" if terminate else session.get("observed_state", "provisioning"),
                    "cleanup_verified": False, "cleanup_status": "provider_blocked",
                    "error_code": "provider_unavailable"}
