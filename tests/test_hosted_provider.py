import unittest
from fork_microscope.hosted.provider import RunPod, FakeRunPod, CreateUncertain
from fork_microscope.hosted.catalog import Catalog
from fork_microscope.hosted.lifecycle import Lifecycle


class HostedProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider = FakeRunPod([{"id": "gpu", "secure": True, "memory": 24,
            "price": {"secure": .5}, "availability": "HIGH"}])
        self.catalog = Catalog(lambda body: self.provider, disk_usd_per_gb_hour=.0001, disk_rate_source="operator configured")
        self.quote = self.catalog.quote({"model_id": self.catalog.models()[0]["id"]}, 10)
        self.session = {"id": "session1", "request_name": "fm-session1", "owner_uid": "u",
            "desired_state": "running", "observed_state": "requested", "expires_at": 1810, "quote": self.quote}
        self.now = 11
        def claim(sid, patch):
            if self.session.get("create_attempted"):
                return False
            self.session.update(patch)
            return True
        self.lifecycle = Lifecycle(lambda s: self.provider, worker_image="example/worker@sha256:" + "a"*64,
            control_plane_url="https://controller.example", persist_before_create=claim,
            worker_environment=lambda s: {"FM_ENROLLMENT_TOKEN": "capability"}, clock=lambda: self.now)

    def tick(self):
        self.session.update(self.lifecycle.reconcile_session(self.session))

    def test_quote_pin_price_availability(self):
        self.assertEqual(self.quote["model_revision"], "989aa7980e4cf806f80c7fef2b1adb7bc71aa306")
        self.assertTrue(self.quote["price_is_estimate"])
        self.provider.catalog[0]["availability"] = "NONE"
        with self.assertRaises(ValueError):
            self.catalog.quote({"model_id": self.quote["model_id"]}, 10)

    def test_uncertain_create_recovers_without_duplicate(self):
        self.provider.uncertain_create = True
        self.tick()
        self.assertTrue(self.session["create_attempted"])
        self.tick()
        self.assertEqual(self.provider.creates, 1)
        self.assertEqual(self.session["provider_ref"], "fake-1")

    def test_deadline_delete_verified_retries(self):
        self.tick()
        self.provider.pods["unrelated"] = {"id": "unrelated", "name": "my-own-pod"}
        self.now = 1811
        self.provider.delete_pending = True
        self.tick()
        self.assertEqual(self.session["observed_state"], "terminating")
        self.provider.delete_pending = False
        self.tick()
        self.assertEqual(self.session["observed_state"], "terminated")
        self.assertIn("unrelated", self.provider.pods)

    def test_uncertain_missing_stays_watched_after_deadline(self):
        self.session["create_attempted"] = True
        self.now = 1811
        self.tick()
        self.assertEqual(self.session["observed_state"], "terminating")
        self.assertEqual(self.provider.creates, 0)

    def test_stale_quote_no_create(self):
        self.now = 131
        self.tick()
        self.assertEqual(self.provider.creates, 0)
        self.assertEqual(self.session["error_code"], "stale_quote")

    def test_provider_running_is_not_worker_ready(self):
        self.tick()
        self.provider.pods["fake-1"]["status"] = "RUNNING"
        self.tick()
        self.assertEqual(self.session["observed_state"], "booting")

    def test_http_exact_v2_paths_redacted_errors(self):
        calls = []
        def transport(method, url, headers, body):
            calls.append((method,url,body))
            return (503, {"detail": "secret"})
        provider = RunPod("private-key", transport)
        with self.assertRaises(CreateUncertain) as context:
            provider.create_pod({"name": "fm-session1"})
        self.assertNotIn("secret", str(context.exception))
        self.assertNotIn("private-key", str(context.exception))
        self.assertEqual(calls[0][1], "https://api.runpod.io/v2/pods")

    def test_worker_image_and_origin_validation(self):
        with self.assertRaises(ValueError):
            Lifecycle(None, worker_image="latest", control_plane_url="https://controller.example", persist_before_create=None, worker_environment=None)


if __name__ == "__main__":
    unittest.main()
