"""Hosted assembly tests: env validation, OIDC-separated controller, public proxy, simulated end to end.

No network and no cloud SDK: Google, RunPod and ID-token verification are explicit injected fakes.
"""
import hashlib, json, sys, unittest, urllib.parse, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from fastapi.testclient import TestClient
except ImportError:  # optional hosted extras not installed
    raise unittest.SkipTest("fastapi/httpx not installed")

from fork_microscope.hosted import bootstrap as bs, drive as dr
from fork_microscope.hosted.credentials import HttpResponse, StreamResponse, VaultError, InMemoryVault
from fork_microscope.hosted.provider import FakeRunPod
from fork_microscope.hosted.service import HostedError

ROOT = Path(__file__).resolve().parents[1]
QWEN = "Qwen/Qwen2.5-1.5B-Instruct"
PIN = "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"
GPU = {"id": "NVIDIA A40", "secure": True, "memory": 48, "availability": "HIGH", "price": {"secure": 0.4}}
RUNPOD_KEY = "rp_super_secret_key"


class FakeGoogle:
    """Just enough Google (OAuth, Drive resumable upload, download) for the Drive adapter."""
    def __init__(self):
        self.content, self.ref, self.log = None, None, []

    def request(self, method, url, headers=None, body=None, timeout=30.0):
        self.log.append((method, url))
        u = urllib.parse.urlsplit(url)
        J = lambda status, obj, h=None: HttpResponse(status, h or {}, json.dumps(obj).encode())
        if u.path == "/token":
            form = urllib.parse.parse_qs(body.decode())
            if form["grant_type"] == ["authorization_code"]:
                if form["code"] != ["goodcode"]:
                    return J(400, {"error": "invalid_grant"})
                return J(200, {"access_token": "AT", "refresh_token": "RT-secret", "expires_in": 3600, "scope": dr.SCOPE})
            return J(200, {"access_token": "AT2", "expires_in": 3600})
        if u.path == "/revoke":
            return J(200, {})
        if method == "POST" and u.path == "/drive/v3/files":
            return J(200, {"id": "FOLDER"})
        if method == "POST" and u.path == "/upload/drive/v3/files":
            self.ref = json.loads(body)["appProperties"]["fm_file_ref"]
            return J(200, {}, {"Location": "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&upload_id=UP1"})
        if method == "PUT":
            return J(200, {"id": "FILE1"}) if self.content is not None else HttpResponse(308, {}, b"")
        if method == "GET" and u.path == "/drive/v3/files/FILE1":
            return J(200, {"id": "FILE1", "size": str(len(self.content)), "parents": ["FOLDER"],
                           "appProperties": {"fm_file_ref": self.ref}, "sha256Checksum": hashlib.sha256(self.content).hexdigest()})
        return J(404, {})

    def stream(self, method, url, headers=None, body=None, timeout=60.0):
        return StreamResponse(200, {}, iter([self.content[:3], self.content[3:]]))


def valid_config():
    c = json.loads((ROOT / "configs" / "investigation-example.json").read_text())
    c["model"].update(model_id=QWEN, revision=PIN)
    c["limits"]["max_seconds"] = 100
    c["lens"] = None  # lens profiles exist only for non-hosted models
    return c


class AlnumRunPod(FakeRunPod):
    """Real RunPod pod ids are alphanumeric; core builds the device proxy host from them."""
    def create_pod(self, body):
        pod = super().create_pod(body)
        stored = self.pods.pop(pod["id"])
        stored["id"] = pod["id"] = pod["id"].replace("-", "")
        self.pods[stored["id"]] = stored
        return pod


class Harness(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.google, self.runpod = FakeGoogle(), AlnumRunPod([GPU])
        users = {"alice-token": {"uid": "alice", "email": "alice@example.test"},
                 "bob-token": {"uid": "bob", "email": "bob@example.test"}}
        self.apps = bs.create_test_apps(users=users, runpod=self.runpod, google_transport=self.google, clock=lambda: self.now[0])
        self.public = TestClient(self.apps.public, base_url="https://app.example.test")
        self.controller = TestClient(self.apps.controller, base_url="https://controller.example.test")
        self.n = 0

    def api(self, method, path, body=None, token="alice-token", headers=None, **kw):
        self.n += 1
        h = {"Authorization": "Bearer " + token, "Idempotency-Key": "k%d" % self.n}
        h.update(headers or {})
        return self.public.request(method, bs.BASE + path, json=body, headers=h, **kw)

    def reconcile(self):
        r = self.controller.post("/internal/reconcile", headers=self.apps.internal("scheduler"))
        self.assertEqual(r.status_code, 200, r.text)

    def start_session(self, mode, token="alice-token"):
        self.assertEqual(self.api("PUT", "/connections/runpod", {"api_key": RUNPOD_KEY}, token).status_code, 200)
        quote = self.api("POST", "/quotes", {"model_id": QWEN, "max_duration_seconds": 1800}, token)
        self.assertEqual(quote.status_code, 200, quote.text)
        body = {"quote_id": quote.json()["id"], "storage_mode": mode}
        if mode == "device":
            body["acknowledge_device_loss"] = True
        r = self.api("POST", "/sessions", body, token)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"], quote.json()

    def boot_worker(self, sid):
        """Reconcile creates the pod; the worker then enrolls with the token from its environment."""
        self.reconcile()
        pod = next(iter(self.runpod.pods.values()))
        r = self.public.post(bs.BASE + "/worker/enroll", json={"session_id": sid, "token": pod["env"]["FM_ENROLLMENT_TOKEN"]})
        self.assertEqual(r.status_code, 200, r.text)
        return pod, dict(session_id=sid, worker_token=r.json()["worker_token"], epoch=r.json()["epoch"])

    def run_job(self, sid, env):
        job = self.api("POST", "/jobs", {"session_id": sid, "command": {"config": valid_config()}})
        self.assertEqual(job.status_code, 200, job.text)
        poll = self.public.post(bs.BASE + "/worker/poll", json=env).json()
        self.assertEqual(poll["action"], "run")
        return poll["job_id"]


class ConfigTests(unittest.TestCase):
    def env(self, **kw):
        return bs.sample_environment(**kw)

    def test_valid_roles(self):
        self.assertEqual(bs.PublicConfig.from_env(self.env()).invited_emails, ("alice@example.test", "bob@example.test"))
        self.assertEqual(bs.ControllerConfig.from_env(self.env()).disk_gb, 30)

    def test_reports_names_not_values(self):
        env = self.env(FM_PUBLIC_URL="http://plain.example.test", FM_WORKER_IMAGE="img:latest",
                       FM_DRIVE_CLIENT_SECRET="hunter2-secret", FM_DISK_USD_PER_GB_HOUR="-1")
        for cls in (bs.PublicConfig, bs.ControllerConfig):
            with self.assertRaises(bs.ConfigError) as cm:
                cls.from_env(env)
            self.assertIn("FM_PUBLIC_URL", str(cm.exception))
            for leaked in ("hunter2", "plain.example", "img:latest"):
                self.assertNotIn(leaked, str(cm.exception))
        with self.assertRaises(bs.ConfigError) as cm:
            bs.ControllerConfig.from_env(env)
        self.assertIn("FM_WORKER_IMAGE", str(cm.exception)); self.assertIn("FM_DISK_USD_PER_GB_HOUR", str(cm.exception))

    def test_required_and_explicit_prices(self):
        for name in ("FM_DISK_USD_PER_GB_HOUR", "FM_DISK_RATE_SOURCE", "FM_WORKER_IMAGE", "FM_PUBLIC_SERVICE_ACCOUNT_EMAIL"):
            env = self.env(); del env[name]
            with self.assertRaises(bs.ConfigError, msg=name):
                bs.ControllerConfig.from_env(env)
        for name in ("FM_INVITED_EMAILS", "FM_FIREBASE_PROJECT_ID", "FM_CONTROLLER_URL", "FM_DRIVE_POST_AUTH_URL"):
            env = self.env(); del env[name]
            with self.assertRaises(bs.ConfigError, msg=name):
                bs.PublicConfig.from_env(env)

    def test_redirects_and_identities_are_exact(self):
        with self.assertRaises(bs.ConfigError):
            bs.PublicConfig.from_env(self.env(FM_DRIVE_REDIRECT_URI="https://evil.example.test" + bs.CALLBACK_PATH))
        with self.assertRaises(bs.ConfigError):
            bs.PublicConfig.from_env(self.env(FM_DRIVE_POST_AUTH_URL="https://evil.example.test/hosted.html"))
        sa = "x@fm-test-project.iam.gserviceaccount.com"
        with self.assertRaises(bs.ConfigError):
            bs.ControllerConfig.from_env(self.env(FM_PUBLIC_SERVICE_ACCOUNT_EMAIL=sa, FM_SCHEDULER_SERVICE_ACCOUNT_EMAIL=sa))
        with self.assertRaises(bs.ConfigError):
            bs.ControllerConfig.from_env(self.env(FM_PUBLIC_SERVICE_ACCOUNT_EMAIL="attacker@gmail.com"))
        with self.assertRaises(bs.ConfigError):
            bs.ControllerConfig.from_env(self.env(FM_HOSTED_ENV="dev"))

    def test_drive_secret_sources(self):
        import tempfile, os
        env = self.env(); del env["FM_DRIVE_CLIENT_SECRET"]
        with self.assertRaises(bs.ConfigError):
            bs.PublicConfig.from_env(env)
        with self.assertRaises(bs.ConfigError):  # both set
            bs.PublicConfig.from_env(self.env(FM_DRIVE_CLIENT_SECRET_FILE="/etc/hostname"))
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("mounted-secret\n")
        try:
            cfg = bs.PublicConfig.from_env(dict(env, FM_DRIVE_CLIENT_SECRET_FILE=f.name))
            self.assertEqual(cfg.drive_client_secret().reveal(), "mounted-secret")
            self.assertNotIn("mounted-secret", repr(cfg))
        finally:
            os.unlink(f.name)
        with self.assertRaises(bs.ConfigError):
            bs.PublicConfig.from_env(dict(env, FM_DRIVE_CLIENT_SECRET_FILE="relative/path"))

    def test_factories_validate_before_touching_cloud(self):
        with self.assertRaises(bs.ConfigError):
            bs.create_public_app({})
        with self.assertRaises(bs.ConfigError):
            bs.create_controller_app({})


class ControllerAuthTests(Harness):
    def post(self, path, headers=None, body=None):
        return self.controller.post("/internal/" + path, json=body if body is not None else {"owner_uid": "alice"}, headers=headers or {})

    def test_requires_valid_google_identity(self):
        self.assertEqual(self.post("drive/status").status_code, 401)
        self.assertEqual(self.post("drive/status", {"Authorization": "Bearer garbage"}).status_code, 401)
        self.assertEqual(self.post("drive/status", {"Authorization": "Basic abc"}).status_code, 401)
        self.assertEqual(self.post("drive/status", self.apps.internal("public")).status_code, 200)

    def test_identities_are_not_interchangeable(self):
        self.assertEqual(self.post("drive/status", self.apps.internal("scheduler")).status_code, 403)
        self.assertEqual(self.post("reconcile", self.apps.internal("public"), {}).status_code, 403)
        other = {"Authorization": "Bearer oidc:rogue@fm-test-project.iam.gserviceaccount.com"}
        self.assertEqual(self.post("reconcile", other, {}).status_code, 403)

    def test_verifier_checks_audience_issuer_verified_email(self):
        good = {"aud": "A", "iss": "https://accounts.google.com", "email": "S@x.iam.gserviceaccount.com", "email_verified": True}
        self.assertEqual(bs.OidcVerifier("A", lambda t, a: dict(good)).email("t"), "s@x.iam.gserviceaccount.com")
        for bad in (dict(good, aud="B"), dict(good, iss="https://evil"), dict(good, email_verified=False), {k: v for k, v in good.items() if k != "email"}):
            with self.assertRaises(HostedError, msg=bad):
                bs.OidcVerifier("A", lambda t, a, bad=bad: bad).email("t")

        def boom(token, audience):
            raise ValueError("signature secret detail")
        with self.assertRaises(HostedError) as cm:
            bs.OidcVerifier("A", boom).email("t")
        self.assertNotIn("secret", cm.exception.message)

    def test_payloads_strict_and_openapi_hidden(self):
        h = self.apps.internal("public")
        self.assertEqual(self.post("drive/status", h, {"owner_uid": "alice", "extra": 1}).status_code, 422)
        self.assertEqual(self.post("drive/status", h, {"owner_uid": "../x"}).status_code, 422)
        self.assertEqual(self.post("catalog/quote", h, {"owner_uid": "alice", "credential_ref": "sv1:" + "0" * 32}).status_code, 422)
        self.assertEqual(self.controller.get("/openapi.json").status_code, 404)
        self.assertEqual(self.controller.get("/api/hosted/v1/me").status_code, 404)  # no user routes on the controller


class PublicSurfaceTests(Harness):
    def test_authentication_required_everywhere(self):
        for method, path in (("GET", "/me"), ("GET", "/connections/drive"), ("POST", "/connections/drive/begin"),
                             ("POST", "/connections/drive/disconnect"), ("GET", "/artifacts/abc/content"), ("GET", "/models")):
            self.assertEqual(self.public.request(method, bs.BASE + path).status_code, 401, path)
        self.assertEqual(self.api("GET", "/me", token="nope").status_code, 401)
        self.assertEqual(self.public.get("/openapi.json").status_code, 404)
        self.assertEqual(self.public.post("/internal/reconcile").status_code, 404)

    def test_catalog_is_pinned_qwen_only(self):
        models = self.api("GET", "/models").json()["models"]
        self.assertEqual([(m["id"], m["revision"]) for m in models], [(QWEN, PIN)])
        self.api("PUT", "/connections/runpod", {"api_key": RUNPOD_KEY})
        r = self.api("POST", "/quotes", {"model_id": "meta-llama/Llama-3-8B", "max_duration_seconds": 1800})
        self.assertEqual(r.status_code, 422)

    def test_public_identity_cannot_read_secrets(self):
        vault = self.apps.public.state.service.vault
        ref = vault.put("alice", "runpod", RUNPOD_KEY)
        with self.assertRaises(VaultError):
            vault.get(ref)
        self.assertEqual(self.apps.vault.get(ref, owner="alice"), RUNPOD_KEY)  # the controller's view can

    def test_quote_needs_connected_runpod_and_is_owner_scoped(self):
        r = self.api("POST", "/quotes", {"model_id": QWEN, "max_duration_seconds": 1800})
        self.assertEqual((r.status_code, r.json()["detail"]), (409, "Connect RunPod first"))
        sid, quote = self.start_session("device")
        self.assertEqual(quote["gpu_id"], "NVIDIA A40")
        self.assertEqual(self.api("GET", "/sessions/" + sid, token="bob-token").status_code, 404)
        self.assertEqual(self.api("POST", "/quotes", {"model_id": QWEN}, token="bob-token").status_code, 409)  # bob has no key

    def test_provider_error_is_generic(self):
        self.api("PUT", "/connections/runpod", {"api_key": RUNPOD_KEY})
        self.runpod.gpus = lambda: (_ for _ in ()).throw(bs.ProviderError(500))
        r = self.api("POST", "/quotes", {"model_id": QWEN, "max_duration_seconds": 1800})
        self.assertEqual(r.status_code, 503); self.assertNotIn(RUNPOD_KEY, r.text)


class DeviceFlowTests(Harness):
    def test_request_then_scheduler_provisions_and_job_runs(self):
        sid, quote = self.start_session("device")
        self.assertEqual(self.api("GET", "/sessions/" + sid).json()["observed_state"], "requested")
        self.assertEqual(self.runpod.creates, 0)  # a request, not immediate provisioning
        pod, env = self.boot_worker(sid)
        self.assertEqual(self.runpod.creates, 1)
        e = pod["env"]
        self.assertEqual(e["FM_CONTROL_PLANE_URL"], "https://app.example.test"); self.assertEqual(e["FM_DASHBOARD_ORIGIN"], "https://app.example.test")
        self.assertEqual(e["FM_MODEL_REVISION"], PIN)
        self.assertEqual(pod["image"], self.apps.controller_config.worker_image)
        blob = json.dumps(pod)
        for secret in (RUNPOD_KEY, "RT-secret", "test-client-secret"):
            self.assertNotIn(secret, blob)
        self.assertEqual(self.public.post(bs.BASE + "/worker/enroll", json={"session_id": sid, "token": e["FM_ENROLLMENT_TOKEN"]}).status_code, 401)  # one use
        job = self.run_job(sid, env)
        art = uuid.uuid4().hex; sha = hashlib.sha256(b"x").hexdigest()
        rep = self.public.post(bs.BASE + "/worker/report", json=dict(env, job_id=job, status="completed", artifacts=[
            {"id": art, "sha256": sha, "size_bytes": 10, "download_token": "t" * 43, "destination": "device"}]))
        self.assertEqual(rep.status_code, 200, rep.text)
        listing = self.api("GET", "/artifacts").json()["artifacts"]
        self.assertEqual([a["id"] for a in listing], [art]); self.assertNotIn("download_token", json.dumps(listing))
        self.assertIn("download_token", self.api("GET", "/artifacts/%s/download" % art).json())
        self.assertEqual(self.api("GET", "/artifacts/%s/content" % art).status_code, 409)  # device bytes never relayed
        self.assertEqual(self.api("GET", "/artifacts/%s/download" % art, token="bob-token").status_code, 404)

    def test_worker_prepare_refused_for_device_sessions(self):
        sid, _ = self.start_session("device")
        _, env = self.boot_worker(sid)
        job = self.run_job(sid, env)
        r = self.public.post(bs.BASE + "/worker/artifact-prepare", json=dict(env, job_id=job, artifact={"id": uuid.uuid4().hex, "sha256": "0" * 64, "size_bytes": 5}))
        self.assertEqual(r.status_code, 409)

    def test_terminate_cleans_up_without_worker_or_browser(self):
        sid, _ = self.start_session("device")
        self.boot_worker(sid)
        self.assertEqual(self.api("POST", "/sessions/%s/terminate" % sid, {}).status_code, 200)
        self.reconcile()
        self.assertEqual(self.runpod.pods, {})
        self.assertEqual(self.api("GET", "/sessions/" + sid).json()["observed_state"], "terminated")

    def test_deadline_cleanup_by_scheduler(self):
        sid, _ = self.start_session("device")
        self.boot_worker(sid)
        self.now[0] += 3600
        self.reconcile(); self.reconcile()
        self.assertEqual(self.runpod.pods, {})

    def test_provider_factory_uses_session_ref_then_current_connection(self):
        sid, _ = self.start_session("device")
        session = self.apps.store.transaction(lambda tx: tx.get("sessions", sid))
        factory = bs.make_provider_factory(self.apps.store, self.apps.vault, lambda key: key)
        self.assertEqual(factory(session), RUNPOD_KEY)
        self.apps.vault.delete(session["credential_ref"])
        with self.assertRaises(bs.RunPodNotConnected):  # session's key gone; connection points at the same dead ref
            factory(session)
        new = self.apps.vault.put("alice", "runpod", "rp_replacement_key")
        self.apps.store.transaction(lambda tx: tx.set("connections", "alice_runpod", {"owner_uid": "alice", "credential_ref": new, "connected": True}))
        self.assertEqual(factory(session), "rp_replacement_key")
        self.assertEqual(factory({"owner_uid": "alice", "credential_ref": session["credential_ref"]}), "rp_replacement_key")
        with self.assertRaises(bs.RunPodNotConnected):
            factory({"owner_uid": "bob"})  # another owner's connection is never used
        other = self.apps.vault.put("bob", "runpod", "bob-key")
        self.assertEqual(factory(dict(session, credential_ref=other)), "rp_replacement_key")  # bob's ref is unreadable as alice


class DriveFlowTests(Harness):
    COOKIE = "__Host-fm_drive_oauth"

    def begin(self, token="alice-token"):
        r = self.api("POST", "/connections/drive/begin", token=token)
        self.assertEqual(r.status_code, 200, r.text)
        url = urllib.parse.urlsplit(r.json()["authorization_url"])
        q = urllib.parse.parse_qs(url.query)
        cookie = r.headers["set-cookie"]
        return q, cookie, cookie.split(";")[0].split("=", 1)[1]

    def callback(self, state, code="goodcode", cookie=None, extra=""):
        self.public.cookies.clear()  # send only the cookie each case names, not the client's jar
        h = {"Cookie": "%s=%s" % (self.COOKIE, cookie)} if cookie else {}
        return self.public.get(bs.CALLBACK_PATH + "?state=%s&code=%s%s" % (state, code, extra), headers=h, follow_redirects=False)

    def connect(self, token="alice-token"):
        q, _, binding = self.begin(token)
        r = self.callback(q["state"][0], cookie=binding)
        self.assertEqual(r.headers["location"], "https://app.example.test/hosted.html?drive=connected", r.text)

    def test_begin_sets_hardened_cookie_and_requested_scope(self):
        q, cookie, _ = self.begin()
        for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age=600"):
            self.assertIn(flag, cookie)
        self.assertTrue(cookie.startswith(self.COOKIE + "="))
        self.assertEqual((q["scope"], q["access_type"], q["prompt"], q["code_challenge_method"]),
                         ([dr.SCOPE], ["offline"], ["consent"], ["S256"]))
        self.assertEqual(q["redirect_uri"], ["https://app.example.test" + bs.CALLBACK_PATH])

    def test_callback_binding_replay_and_open_redirect(self):
        q, _, binding = self.begin()
        state = q["state"][0]
        ok = "https://app.example.test/hosted.html?drive=connected"
        err = "https://app.example.test/hosted.html?drive=error"
        self.assertEqual(self.callback(state).headers["location"], err)  # no cookie
        self.assertEqual(self.callback(state, cookie="wrong").headers["location"], err)  # state burned by the mismatch
        self.assertEqual(self.callback(state, cookie=binding).headers["location"], err)
        q, _, binding = self.begin()
        r = self.callback(q["state"][0], cookie=binding, extra="&redirect=https://evil.example&next=//evil")
        self.assertEqual(r.status_code, 303); self.assertEqual(r.headers["location"], ok)
        self.assertEqual(r.headers["referrer-policy"], "no-referrer"); self.assertIn("Max-Age=0", r.headers["set-cookie"])
        self.assertEqual(self.callback(q["state"][0], cookie=binding).headers["location"], err)  # single use
        q, _, binding = self.begin()
        self.assertEqual(self.callback(q["state"][0], cookie=binding, extra="&error=access_denied").headers["location"], err)
        q, _, binding = self.begin()
        self.assertEqual(self.callback(q["state"][0], code="badcode", cookie=binding).headers["location"], err)

    def test_state_is_owner_bound_to_the_initiator(self):
        self.connect("alice-token")
        self.assertTrue(self.api("GET", "/connections/drive").json()["connected"])
        self.assertFalse(self.api("GET", "/connections/drive", token="bob-token").json()["connected"])
        text = json.dumps(self.api("GET", "/connections/drive").json())
        for hidden in ("RT-secret", "FOLDER", "sv1:", "AT"):
            self.assertNotIn(hidden, text)

    def test_disconnect_revokes(self):
        self.connect()
        self.assertTrue(self.api("POST", "/connections/drive/disconnect").json()["revoked_remote"])
        self.assertFalse(self.api("GET", "/connections/drive").json()["connected"])

    def test_drive_session_upload_verify_and_download_after_termination(self):
        self.connect()
        sid, _ = self.start_session("drive")
        pod, env = self.boot_worker(sid)
        job = self.run_job(sid, env)
        data = b'{"evidence": "bundle"}'
        sha, art = hashlib.sha256(data).hexdigest(), uuid.uuid4().hex
        meta = {"id": art, "sha256": sha, "size_bytes": len(data)}
        prep = lambda: self.public.post(bs.BASE + "/worker/artifact-prepare", json=dict(env, job_id=job, artifact=meta))
        grant = prep().json()
        self.assertTrue(grant["upload_url"].startswith(bs.DRIVE_UPLOAD_PREFIX)); self.assertTrue(grant["file_ref"].startswith("df_"))
        self.assertEqual(prep().json()["file_ref"], grant["file_ref"])  # worker retry is idempotent
        self.assertEqual(self.public.post(bs.BASE + "/worker/artifact-prepare", json=dict(env, job_id=job, artifact=dict(meta, sha256="1" * 64))).status_code, 409)
        # Completion before the bytes reached Drive must not be accepted.
        report = dict(env, job_id=job, status="completed", artifacts=[dict(meta, file_ref="WORKER-REPORTED-DRIVE-ID", destination="drive")])
        self.assertEqual(self.public.post(bs.BASE + "/worker/report", json=report).status_code, 400)
        self.google.content = data  # the worker uploads to the per-file session URL
        done = self.public.post(bs.BASE + "/worker/report", json=report)
        self.assertEqual(done.status_code, 200, done.text)
        artifacts = self.api("GET", "/artifacts").json()["artifacts"]
        self.assertEqual([a["file_ref"] for a in artifacts], [grant["file_ref"]])  # never the worker/Drive id
        self.assertNotIn("FILE1", json.dumps(artifacts)); self.assertNotIn("upload_id", json.dumps(artifacts))
        # Pod gone and session terminated: Drive evidence is still downloadable by its owner only.
        self.api("POST", "/sessions/%s/terminate" % sid, {}); self.reconcile()
        self.assertEqual(self.runpod.pods, {})
        got = self.api("GET", "/artifacts/%s/content" % art)
        self.assertEqual((got.status_code, got.content), (200, data))
        self.assertEqual(got.headers["cache-control"], "no-store"); self.assertEqual(got.headers["content-length"], str(len(data)))
        self.assertEqual(self.api("GET", "/artifacts/%s/content" % art, token="bob-token").status_code, 404)
        self.assertEqual(self.api("GET", "/artifacts/%s/content" % uuid.uuid4().hex).status_code, 404)

    def test_checksum_mismatch_rejected(self):
        self.connect()
        sid, _ = self.start_session("drive")
        _, env = self.boot_worker(sid)
        job = self.run_job(sid, env)
        meta = {"id": uuid.uuid4().hex, "sha256": "2" * 64, "size_bytes": 9}
        self.assertEqual(self.public.post(bs.BASE + "/worker/artifact-prepare", json=dict(env, job_id=job, artifact=meta)).status_code, 200)
        self.google.content = b"123456789"  # right size, wrong digest
        r = self.public.post(bs.BASE + "/worker/report", json=dict(env, job_id=job, status="completed", artifacts=[meta]))
        self.assertEqual(r.status_code, 409); self.assertEqual(self.api("GET", "/artifacts").json()["artifacts"], [])

    def test_worker_cannot_prepare_for_another_owners_job(self):
        self.connect("alice-token")
        sid, _ = self.start_session("drive")
        _, env = self.boot_worker(sid)
        job = self.run_job(sid, env)
        direct = self.controller.post("/internal/drive/artifact-prepare", headers=self.apps.internal("public"), json={
            "owner_uid": "bob", "session_id": sid, "job_id": job, "artifact": {"id": uuid.uuid4().hex, "sha256": "3" * 64, "size_bytes": 4}})
        self.assertEqual(direct.status_code, 404)


class ClientTests(unittest.TestCase):
    class Transport:
        def __init__(self, response=None, error=None, stream=None):
            self.response, self.error, self.stream_response, self.calls = response, error, stream, []

        def request(self, method, url, headers=None, body=None, timeout=30.0):
            self.calls.append((method, url, dict(headers), body, timeout))
            if self.error:
                raise self.error
            return self.response

        def stream(self, method, url, headers=None, body=None, timeout=60.0):
            self.calls.append((method, url, dict(headers), body, timeout))
            return self.stream_response

    def client(self, transport, token=lambda: "tok"):
        return bs.ControllerClient("https://controller.example.test/", token, transport)

    def test_authenticated_bounded_call(self):
        t = self.Transport(HttpResponse(200, {}, b'{"ok": true}'))
        self.assertEqual(self.client(t).call("drive/status", {"owner_uid": "a"}), {"ok": True})
        method, url, headers, body, timeout = t.calls[0]
        self.assertEqual((method, url, headers["Authorization"], timeout), ("POST", "https://controller.example.test/internal/drive/status", "Bearer tok", 15.0))

    def test_errors_never_leak(self):
        leak = "SECRET-TOKEN-xyz"
        cases = [self.Transport(error=RuntimeError(leak)), self.Transport(HttpResponse(500, {}, leak.encode())),
                 self.Transport(HttpResponse(401, {}, b'{"detail": "%s"}' % leak.encode())), self.Transport(HttpResponse(200, {}, b"not json")),
                 self.Transport(HttpResponse(200, {}, b"[1]"))]
        for t in cases:
            with self.assertRaises(HostedError) as cm:
                self.client(t).call("x", {})
            self.assertEqual((cm.exception.status, cm.exception.message), (503, "Hosted integration unavailable"))
        with self.assertRaises(HostedError) as cm:  # token provider failure
            self.client(self.Transport(), lambda: (_ for _ in ()).throw(RuntimeError(leak))).call("x", {})
        self.assertNotIn(leak, cm.exception.message)
        with self.assertRaises(HostedError) as cm:
            self.client(self.Transport(HttpResponse(409, {}, b'{"detail": "Connect RunPod first"}'))).call("x", {})
        self.assertEqual((cm.exception.status, cm.exception.message), (409, "Connect RunPod first"))

    def stream(self, headers, chunks, status=200):
        closed = []
        r = StreamResponse(status, headers, iter(chunks), lambda: closed.append(1))
        return self.client(self.Transport(stream=r)).stream("drive/download", {}), closed

    def test_stream_length_enforced(self):
        (headers, chunks), closed = self.stream({"Content-Length": "6", "Content-Type": "application/json", "X-Evil": "1"}, [b"abc", b"def"])
        self.assertEqual((b"".join(chunks), "X-Evil" in headers), (b"abcdef", False)); self.assertTrue(closed)
        for declared, parts in (("6", [b"abc"]), ("3", [b"abcdef"])):
            (_, chunks), closed = self.stream({"Content-Length": declared}, parts)
            with self.assertRaises(RuntimeError):
                b"".join(chunks)
            self.assertTrue(closed)
        with self.assertRaises(HostedError):
            self.stream({}, [b"x"])  # no Content-Length: refuse to relay
        with self.assertRaises(HostedError):
            self.stream({"Content-Length": str(10 ** 12)}, [b"x"])

    def test_remote_catalog_rejects_unpinned_quote(self):
        good = {"model_id": QWEN, "model_revision": PIN}
        t = self.Transport(HttpResponse(200, {}, json.dumps(good).encode()))
        self.assertEqual(bs.RemoteCatalog(self.client(t)).quote({"owner_uid": "a", "model_id": QWEN, "junk": 1}, 1.0), good)
        self.assertEqual(json.loads(t.calls[0][3]), {"owner_uid": "a", "model_id": QWEN})  # whitelist only
        bad = self.Transport(HttpResponse(200, {}, json.dumps(dict(good, model_revision="main")).encode()))
        with self.assertRaises(HostedError):
            bs.RemoteCatalog(self.client(bad)).quote({"owner_uid": "a"}, 1.0)


if __name__ == "__main__":
    unittest.main()
