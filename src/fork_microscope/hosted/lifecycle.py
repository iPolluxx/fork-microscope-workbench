"""Controller reconciliation: durable create claim, absolute deadlines, verified deletion.

Invoke periodically from an independent controller; browser and worker heartbeats
are never required for cleanup. An uncertain create is reconciled by exact name,
never repeated. Missing pods after uncertain create remain watched indefinitely.
"""
import re
import time
from urllib.parse import urlsplit
from .provider import CreateUncertain, ProviderError


class Lifecycle:
    def __init__(self, provider_factory, *, worker_image, control_plane_url,
                 persist_before_create, worker_environment, clock=time.time, dashboard_origin=None,
                 heartbeat_timeout_seconds=120):
        if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", worker_image):
            raise ValueError("Immutable worker image digest required")
        url = urlsplit(control_plane_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Configured HTTPS control plane required")
        origin = urlsplit(dashboard_origin or control_plane_url)
        if origin.scheme != "https" or not origin.hostname or origin.username or origin.password or origin.query or origin.fragment or origin.path not in {"", "/"}:
            raise ValueError("Configured HTTPS dashboard origin required")
        self.dashboard_origin = f"https://{origin.netloc}"
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
        provider = self.provider_factory(session)
        name = session["request_name"]
        if not re.fullmatch(r"fm-[a-zA-Z0-9_-]{8,120}", name):
            raise ValueError("Invalid managed request name")
        try:
            pods = [pod for pod in provider.list_pods() if pod.get("name") == name]
            ref = session.get("provider_ref")
            if ref:
                referenced = provider.get_pod(ref)
                if referenced and referenced.get("name") != name:
                    return {"observed_state": "failed", "error_code": "provider_ownership_mismatch"}
                if referenced and not any(p["id"] == ref for p in pods):
                    pods.append(referenced)
            if terminate:
                for pod in pods:
                    provider.delete_pod(pod["id"])
                remaining = [p for p in provider.list_pods() if p.get("name") == name]
                remaining.extend(p for p in (provider.get_pod(pod["id"]) for pod in pods) if p)
                # If a POST crashed, absence is not proof it will never appear.
                uncertain = session.get("create_attempted") and not session.get("provider_ref") and not pods
                return {"desired_state": "terminated", "observed_state": "terminating" if remaining or uncertain else "terminated",
                        "cleanup_verified_at": None if remaining or uncertain else now,
                        **({"error_code": deadline_code} if deadline_code else {})}
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
            if session.get("create_attempted"):
                return {"observed_state": "provisioning", "error_code": "create_outcome_pending"}
            quote = session["quote"]
            if now >= quote["expires_at"]:
                return {"desired_state": "terminated", "observed_state": "failed", "error_code": "stale_quote"}
            env = self.environment(session)
            if any("RUNPOD" in key.upper() or "DRIVE" in key.upper() for key in env):
                raise ValueError("Provider and Drive credentials forbidden in worker environment")
            if not self.claim(session["id"], {"create_attempted": True, "observed_state": "provisioning"}):
                return {"observed_state": "provisioning"}
            body = {"name": name, "image": self.image, "gpu": {"id": quote["gpu_id"], "count": 1},
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
                return {"desired_state": "terminated", "observed_state": "terminating", "error_code": "create_rejected"}
            return {"provider_ref": pod["id"], "observed_state": "booting"}
        except ProviderError:
            return {"observed_state": "terminating" if terminate else session.get("observed_state", "provisioning"),
                    "error_code": "provider_unavailable"}
