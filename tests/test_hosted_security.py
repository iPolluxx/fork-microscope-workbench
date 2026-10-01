"""Hosted security tests: Firebase verification, vault, Drive OAuth/upload. No network."""
import base64, hashlib, json, sys, unittest, urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fork_microscope.hosted import auth, credentials as cr, drive as dr
from fork_microscope.hosted.credentials import HttpResponse, StreamResponse, Secret

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.x509.oid import NameOID
    import datetime
except ImportError:  # pragma: no cover
    x509 = None

PROJECT = "fm-test-project"
NOW = 1_800_000_000


def b64(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class Keys:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "t")])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(self.key.public_key())
                .serial_number(1).not_valid_before(datetime.datetime(2020, 1, 1)).not_valid_after(datetime.datetime(2040, 1, 1))
                .sign(self.key, hashes.SHA256()))
        self.pem = cert.public_bytes(serialization.Encoding.PEM).decode()

    def token(self, kid="k1", alg="RS256", **over):
        claims = dict(iss="https://securetoken.google.com/" + PROJECT, aud=PROJECT, sub="u1", user_id="u1",
                      iat=NOW - 10, auth_time=NOW - 10, exp=NOW + 3000, email="Ann@Example.com", email_verified=True)
        claims.update(over)
        claims = {k: v for k, v in claims.items() if v is not None}
        head = b64(json.dumps({"alg": alg, "kid": kid}).encode()); body = b64(json.dumps(claims).encode())
        sig = self.key.sign((head + "." + body).encode(), padding.PKCS1v15(), hashes.SHA256())
        return head + "." + body + "." + b64(sig)


class CertTransport:
    def __init__(self, pem): self.pem, self.calls = pem, 0
    def request(self, method, url, headers=None, body=None, timeout=30.0):
        self.calls += 1
        return HttpResponse(200, {"Cache-Control": "max-age=3600"}, json.dumps({"k1": self.pem}).encode())


class Revoker:
    def __init__(self, bad=False): self.bad, self.seen = bad, []
    def check(self, uid, iat):
        self.seen.append((uid, iat))
        if self.bad: raise auth.AuthenticationError("revoked")


@unittest.skipIf(x509 is None, "cryptography not installed")
class FirebaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.keys = Keys()

    def make(self, revoker=None, invites=("ann@example.com",)):
        return auth.FirebaseAuthenticator(PROJECT, auth.StaticInvites(invites), revoker or Revoker(),
                                          CertTransport(self.keys.pem), clock=lambda: NOW)

    def test_valid_identity_and_invite_normalised(self):
        self.assertEqual(self.make().verify(self.keys.token()), {"uid": "u1", "email": "ann@example.com"})

    def test_strict_claims_rejected(self):
        a = self.make()
        for over in (dict(aud="other"), dict(iss="https://securetoken.google.com/other"), dict(exp=NOW - 1),
                     dict(iat=NOW + 9999), dict(sub=""), dict(email_verified=False), dict(email=None),
                     dict(auth_time=None), dict(user_id="u2")):
            with self.assertRaises(auth.AuthenticationError, msg=str(over)):
                a.verify(self.keys.token(**over))

    def test_alg_kid_signature_malformed(self):
        a = self.make()
        for t in (self.keys.token(alg="none"), self.keys.token(kid="zzz"), "a.b", "", None, "x" * 9000):
            with self.assertRaises(auth.AuthenticationError):
                a.verify(t)
        h, b, s = self.keys.token().split(".")
        tampered = h + "." + b64(json.dumps(dict(json.loads(base64.urlsafe_b64decode(b + "==")), sub="u9")).encode()) + "." + s
        with self.assertRaises(auth.AuthenticationError):
            a.verify(tampered)

    def test_not_invited_is_403_and_revoked_denied(self):
        with self.assertRaises(auth.NotInvitedError) as c:
            self.make(invites=("bob@example.com",)).verify(self.keys.token())
        self.assertEqual(c.exception.status, 403)
        with self.assertRaises(auth.AuthenticationError):
            self.make(Revoker(bad=True)).verify(self.keys.token())

    def test_revocation_required_and_cert_cached(self):
        with self.assertRaises(ValueError):
            auth.FirebaseAuthenticator(PROJECT, auth.StaticInvites([]))
        a = self.make(); a.verify(self.keys.token()); a.verify(self.keys.token())
        self.assertEqual(a.http.calls, 1)


class IdentityToolkitTests(unittest.TestCase):
    def rev(self, status, payload):
        class T:
            def request(s, m, u, h=None, b=None, timeout=30.0): return HttpResponse(status, {}, json.dumps(payload).encode())
        return auth.IdentityToolkitRevocation(PROJECT, lambda: "admintoken", T())

    def test_valid_since_disabled_unknown_fail_closed(self):
        ok = {"users": [{"localId": "u1", "validSince": "100"}]}
        self.rev(200, ok).check("u1", 100)
        with self.assertRaises(auth.AuthenticationError): self.rev(200, ok).check("u1", 99)
        with self.assertRaises(auth.AuthenticationError): self.rev(200, {"users": [{"localId": "u1", "disabled": True}]}).check("u1", 500)
        with self.assertRaises(auth.AuthenticationError): self.rev(200, {}).check("u1", 500)
        with self.assertRaises(auth.AuthenticationError): self.rev(500, {}).check("u1", 500)


class FakeAuthTests(unittest.TestCase):
    def test_explicit_only(self):
        a = auth.FakeAuthenticator({"t": {"uid": "u", "email": "e@x.io"}})
        self.assertEqual(a.verify("t")["uid"], "u")
        with self.assertRaises(auth.AuthenticationError): a.verify("nope")
        import os
        os.environ["FM_HOSTED_ENV"] = "production"
        try:
            with self.assertRaises(RuntimeError): auth.FakeAuthenticator({})
        finally:
            del os.environ["FM_HOSTED_ENV"]


class VaultTests(unittest.TestCase):
    def test_memory_owner_and_delete(self):
        v = cr.InMemoryVault(); ref = v.put("alice", "runpod_key", "rk-SECRET-1")
        self.assertNotIn("SECRET", ref)
        self.assertEqual(v.get(ref), "rk-SECRET-1")
        with self.assertRaises(cr.SecretNotFound): v.get(ref, owner="mallory")
        v.delete(ref); v.delete(ref)
        with self.assertRaises(cr.SecretNotFound): v.get(ref)
        with self.assertRaises(cr.VaultError): v.put("a b", "k", "x")

    def test_secret_wrapper_and_redact(self):
        s = Secret("tok-abcdef")
        for out in (repr(s), str(s), "%s" % s, "{}".format(s), repr([s]), repr(cr.InMemoryVault())):
            self.assertNotIn("abcdef", out)
        self.assertEqual(cr.redact("a tok-abcdef b " + urllib.parse.quote("tok/abc"), s, "tok/abc"), "a *** b ***")
        with self.assertRaises(TypeError): json.dumps(s)

    def test_google_vault_rest(self):
        calls = []
        store = {}
        class T:
            def request(s, m, u, h=None, b=None, timeout=30.0):
                calls.append((m, u, h, b)); path = u.split("/v1/")[1]
                if m == "POST" and "?secretId=" in path:
                    store[path.split("=")[1]] = {"labels": json.loads(b)["labels"]}; return HttpResponse(200, {}, b"{}")
                if path.endswith(":addVersion"):
                    store[path.split("/")[3].split(":")[0]]["data"] = json.loads(b)["payload"]["data"]; return HttpResponse(200, {}, b"{}")
                sid = path.split("/")[3]
                if sid not in store: return HttpResponse(404, {}, b"{}")
                if m == "DELETE": del store[sid]; return HttpResponse(200, {}, b"{}")
                if path.endswith(":access"): return HttpResponse(200, {}, json.dumps({"payload": {"data": store[sid]["data"]}}).encode())
                return HttpResponse(200, {}, json.dumps({"labels": store[sid]["labels"]}).encode())
        v = cr.GoogleSecretVault("fm-test-project", T(), lambda: "ADCTOKEN")
        ref = v.put("alice", "drive_refresh", "refresh-XYZ")
        self.assertEqual(v.get(ref, owner="alice"), "refresh-XYZ")
        with self.assertRaises(cr.SecretNotFound): v.get(ref, owner="mallory")
        self.assertNotIn("refresh-XYZ", repr(v) + ref)
        v.delete(ref); v.delete(ref)
        with self.assertRaises(cr.SecretNotFound): v.get(ref)
        with self.assertRaises(cr.SecretNotFound): v.get("../../x")

    def test_urllib_transport_policy(self):
        t = cr.UrllibTransport({"www.googleapis.com"})
        for u in ("http://www.googleapis.com/x", "https://evil.example/x", "https://u:p@www.googleapis.com/x"):
            with self.assertRaises(cr.TransportError): t.request("GET", u)


class FakeGoogle:
    """Routes the Google endpoints used by the Drive adapter; records requests."""
    def __init__(self, content=b'{"evidence": 1}'):
        self.content, self.log = content, []
        self.refresh_ok, self.grant_scope, self.checksum = True, dr.SCOPE, None
        self.received, self.revoked, self.uploaded = 0, False, False
        self.corrupt = False
    def request(self, method, url, headers=None, body=None, timeout=30.0):
        self.log.append((method, url, dict(headers or {}), body))
        u = urllib.parse.urlsplit(url); form = urllib.parse.parse_qs((body or b"").decode()) if method == "POST" and u.path in ("/token", "/revoke") else {}
        J = lambda status, obj, h=None: HttpResponse(status, h or {}, json.dumps(obj).encode())
        if u.path == "/token":
            if form["grant_type"] == ["authorization_code"]:
                if form["code"] != ["goodcode"]: return J(400, {"error": "invalid_grant"})
                return J(200, {"access_token": "AT1", "refresh_token": "RT-secret", "expires_in": 3600, "scope": self.grant_scope})
            if not self.refresh_ok: return J(400, {"error": "invalid_grant"})
            return J(200, {"access_token": "AT2", "expires_in": 3600})
        if u.path == "/revoke": self.revoked = True; return J(200, {})
        if method == "POST" and u.path == "/drive/v3/files": return J(200, {"id": "FOLDER"})
        if method == "POST" and u.path == "/upload/drive/v3/files":
            return J(200, {}, {"Location": "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&upload_id=UP123"})
        if method == "PUT" and "upload_id=UP123" in url:
            if self.uploaded: return J(200, {"id": "FILE1"})
            return HttpResponse(308, {"Range": "bytes=0-9"} if self.received else {}, b"")
        if method == "GET" and u.path == "/drive/v3/files/FILE1":
            size = len(self.content) + (1 if self.corrupt else 0)
            m = {"id": "FILE1", "size": str(size), "parents": ["FOLDER"], "appProperties": {"fm_file_ref": self.ref}}
            if self.checksum: m["sha256Checksum"] = self.checksum
            return J(200, m)
        return J(404, {})
    def stream(self, method, url, headers=None, body=None, timeout=60.0):
        self.log.append((method, url, dict(headers or {}), body))
        data = self.content[:-1] + b"X" if self.corrupt else self.content
        return StreamResponse(200, {}, iter([data[:5], data[5:]]))


class DriveTests(unittest.TestCase):
    def setUp(self):
        self.g, self.docs, self.vault = FakeGoogle(), dr.MemoryDocs(), cr.InMemoryVault()
        self.cfg = dr.DriveConfig("cid.apps.googleusercontent.com", "https://app.example/api/hosted/v1/connections/drive/callback")
        self.t = [1000.0]
        mk = lambda cls, **k: cls(self.docs, self.vault, self.cfg, Secret("CLIENTSECRET"), self.g, lambda: self.t[0], **k)
        self.auth = mk(dr.DriveAuthorization, allow_process_local=True)
        self.ctl = mk(dr.DriveController)

    def connect(self, owner="alice"):
        s = self.auth.begin(owner)
        return self.auth.complete(s.state, "goodcode", s.binding)

    def test_process_local_refused_by_default(self):
        with self.assertRaises(ValueError):
            dr.DriveAuthorization(dr.MemoryDocs(), self.vault, self.cfg, Secret("x"), self.g)

    def test_authorization_url_offline_drive_file_pkce(self):
        s = self.auth.begin("alice"); q = urllib.parse.parse_qs(urllib.parse.urlsplit(s.url).query)
        self.assertEqual(q["scope"], [dr.SCOPE]); self.assertEqual(q["access_type"], ["offline"])
        self.assertEqual(q["code_challenge_method"], ["S256"]); self.assertEqual(q["state"], [s.state])
        self.assertNotIn("client_secret", s.url); self.assertNotIn(s.binding, s.url)

    def test_replay_state_single_use(self):
        s = self.auth.begin("alice")
        self.auth.complete(s.state, "goodcode", s.binding)
        with self.assertRaises(dr.StateError): self.auth.complete(s.state, "goodcode", s.binding)

    def test_csrf_binding_and_owner_and_failure_burns_state(self):
        s = self.auth.begin("alice")
        with self.assertRaises(dr.StateError): self.auth.complete(s.state, "goodcode", "attacker-binding")
        with self.assertRaises(dr.StateError): self.auth.complete(s.state, "goodcode", s.binding)  # burned
        s = self.auth.begin("alice")
        with self.assertRaises(dr.StateError): self.auth.complete(s.state, "goodcode", s.binding, expected_owner="mallory")
        self.assertFalse(self.auth.status("alice")["connected"])
        with self.assertRaises(dr.StateError): self.auth.complete("forged", "goodcode", "b")

    def test_expired_state_and_bad_scope_and_no_refresh(self):
        s = self.auth.begin("alice"); self.t[0] += dr.STATE_TTL + 1
        with self.assertRaises(dr.StateError): self.auth.complete(s.state, "goodcode", s.binding)
        self.g.grant_scope = dr.SCOPE + " https://www.googleapis.com/auth/drive"
        s = self.auth.begin("alice")
        with self.assertRaises(dr.StateError): self.auth.complete(s.state, "goodcode", s.binding)
        self.assertFalse(self.auth.status("alice")["connected"])

    def test_public_output_and_redaction(self):
        pub = self.connect()
        blob = json.dumps(pub) + repr(self.auth) + repr(self.ctl) + json.dumps(self.auth.status("alice"))
        for needle in ("RT-secret", "AT1", "CLIENTSECRET", "sv1:"): self.assertNotIn(needle, blob)
        self.assertNotIn("RT-secret", repr(self.docs._d.values()))  # refresh token only in vault
        self.assertEqual(pub["scope"], dr.SCOPE)

    def register(self, owner="alice", content=None):
        content = content or self.g.content
        f = self.ctl.register_file(owner, "job1", "bundle.json", len(content), hashlib.sha256(content).hexdigest())
        self.g.ref = f["file_ref"]; return f

    def test_owner_isolation_on_files(self):
        self.connect(); self.connect("bob"); f = self.register()
        for call in (self.ctl.create_upload_session, self.ctl.upload_status, self.ctl.confirm, self.ctl.open_download):
            with self.assertRaises(dr.NotFound): call("bob", f["file_ref"])
        with self.assertRaises(dr.NotFound): self.ctl.create_upload_session("alice", "df_unregistered")
        with self.assertRaises(dr.NotFound): self.ctl.register_file("carol", "j", "a.json", 5, "0" * 64)

    def test_registration_validation(self):
        self.connect()
        for args in (("j", "../x", 5, "0" * 64), ("j", "a.json", 0, "0" * 64), ("j", "a.json", 5, "XY"),
                     ("j", "a.json", dr.MAX_ARTIFACT_BYTES + 1, "0" * 64), ("j", "a.json", True, "0" * 64)):
            with self.assertRaises(dr.DriveError): self.ctl.register_file("alice", *args)

    def test_upload_session_confirm_and_download(self):
        self.connect(); f = self.register()
        cap = self.ctl.create_upload_session("alice", f["file_ref"])
        self.assertIn("UP123", cap.url.reveal()); self.assertNotIn("UP123", repr(cap) + json.dumps(self.ctl.public_file(self.ctl._file("alice", f["file_ref"]))))
        post = [l for l in self.g.log if l[1].startswith(dr.UPLOAD)][0]
        self.assertEqual(post[2]["X-Upload-Content-Length"], str(f["size"]))
        self.assertEqual(json.loads(post[3])["parents"], ["FOLDER"])
        with self.assertRaises(dr.DriveError): self.ctl.create_upload_session("alice", f["file_ref"])
        self.assertEqual(self.ctl.upload_status("alice", f["file_ref"])["state"], "pending")
        self.g.received = 1; self.assertEqual(self.ctl.upload_status("alice", f["file_ref"]), {"state": "partial", "received": 10})
        with self.assertRaises(dr.DriveError): self.ctl.confirm("alice", f["file_ref"])
        with self.assertRaises(dr.DriveError): self.ctl.open_download("alice", f["file_ref"])
        self.g.uploaded = True
        self.assertEqual(self.ctl.confirm("alice", f["file_ref"])["status"], "confirmed")  # via streamed hash
        headers, it = self.ctl.open_download("alice", f["file_ref"])
        self.assertEqual(b"".join(it), self.g.content); self.assertEqual(headers["Cache-Control"], "no-store")
        # session capability deleted after confirm; no bearer was sent to the session URL
        self.assertEqual(len(self.vault._items), 1)  # only refresh token remains
        put = [l for l in self.g.log if l[0] == "PUT"][0]; self.assertNotIn("Authorization", put[2])

    def test_integrity_failures(self):
        self.connect(); f = self.register(); self.ctl.create_upload_session("alice", f["file_ref"]); self.g.uploaded = True
        self.g.checksum = "0" * 64
        with self.assertRaises(dr.IntegrityError): self.ctl.confirm("alice", f["file_ref"])
        self.assertEqual(self.ctl._file("alice", f["file_ref"])["status"], "corrupt")
        self.g.checksum = None; self.g.corrupt = True
        f2 = self.register(); self.ctl.create_upload_session("alice", f2["file_ref"])
        with self.assertRaises(dr.IntegrityError): self.ctl.confirm("alice", f2["file_ref"])

    def test_download_detects_tamper_after_confirm(self):
        self.connect(); f = self.register(); self.ctl.create_upload_session("alice", f["file_ref"]); self.g.uploaded = True
        self.g.checksum = hashlib.sha256(self.g.content).hexdigest(); self.ctl.confirm("alice", f["file_ref"])
        self.g.corrupt = True
        with self.assertRaises(dr.IntegrityError): b"".join(self.ctl.open_download("alice", f["file_ref"])[1])

    def test_revoked_access_clears_connection(self):
        self.connect(); f = self.register()
        self.g.refresh_ok = False; self.t[0] += 7200  # expire cached access token
        with self.assertRaises(dr.DriveRevoked): self.ctl.create_upload_session("alice", f["file_ref"])
        self.assertFalse(self.auth.status("alice")["connected"])
        with self.assertRaises(dr.NotFound): self.ctl.access_token("alice")
        with self.assertRaises(dr.NotFound): self.ctl.register_file("alice", "j", "a.json", 5, "0" * 64)
        self.assertEqual(self.vault._items, {})  # refresh secret deleted

    def test_disconnect_revokes_remote_and_deletes_secret(self):
        self.connect(); self.ctl.access_token("alice")
        self.assertEqual(self.ctl.disconnect("alice"), {"revoked_remote": True})
        self.assertTrue(self.g.revoked); self.assertEqual(self.vault._items, {})
        self.assertFalse(self.auth.status("alice")["connected"])
        self.assertEqual(self.ctl.disconnect("alice"), {"revoked_remote": False})
        with self.assertRaises(dr.NotFound): self.ctl.access_token("alice")

    def test_reconnect_replaces_old_secret(self):
        self.connect(); self.connect(); self.assertEqual(len(self.vault._items), 1)

    def test_bad_upload_location_rejected(self):
        self.connect(); f = self.register()
        orig = self.g.request
        def bad(method, url, headers=None, body=None, timeout=30.0):
            if url.startswith(dr.UPLOAD): return HttpResponse(200, {"Location": "https://evil.example/upload?upload_id=1"}, b"{}")
            return orig(method, url, headers, body, timeout)
        self.g.request = bad
        with self.assertRaises(dr.DriveUnavailable): self.ctl.create_upload_session("alice", f["file_ref"])

    def test_authorization_header_never_leaks_in_errors(self):
        self.connect(); f = self.register()
        self.g.request = lambda *a, **k: (_ for _ in ()).throw(cr.TransportError("x"))
        self.ctl._tokens.clear()
        try: self.ctl.create_upload_session("alice", f["file_ref"])
        except dr.DriveError as e: self.assertNotIn("RT-secret", str(e))


if __name__ == "__main__":
    unittest.main()
