import unittest
from fork_microscope.hosted.provider import RunPod, FakeRunPod, CreateUncertain, ProviderError
from fork_microscope.hosted.catalog import Catalog
from fork_microscope.hosted.lifecycle import Lifecycle


class HostedProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider = FakeRunPod([{"id": "gpu", "manufacturer": "NVIDIA", "secure": True, "memory": 24,
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

    def test_live_unknown_catalog_entry_and_cuda_compatibility(self):
        unknown = {"id": "unknown", "manufacturer": "UNKNOWN", "memory": 0,
                   "price": {"secure": 0}, "secure": False, "availability": "NONE"}
        amd = dict(self.provider.catalog[0], id="AMD", manufacturer="AMD", price={"secure": .01})
        rows = [unknown, amd, self.provider.catalog[0]]
        seen = []
        def transport(method, url, headers, body):
            seen.append(headers)
            return 200, {"gpus": rows}
        provider = RunPod("test-key", transport)
        catalog = Catalog(lambda body: provider, disk_usd_per_gb_hour=0, disk_rate_source="test")
        self.assertEqual(catalog.quote({"model_id": self.quote["model_id"]}, 10)["gpu_id"], "gpu")
        self.assertTrue(seen[0]["User-Agent"].startswith("ForkMicroscope/"))

    def test_uncertain_create_recovers_without_duplicate(self):
        self.provider.uncertain_create = True
        self.tick()
        self.assertTrue(self.session["create_attempted"])
        self.tick()
        self.assertEqual(self.provider.creates, 1)
        self.assertEqual(self.session["provider_ref"], "fake1")

    def test_definite_rejection_can_finish_cleanup_without_recreating(self):
        def reject(body):
            raise ProviderError(400)
        self.provider.create_pod = reject
        self.tick()
        self.assertTrue(self.session["create_rejected"])
        self.tick()
        self.assertEqual(self.session["observed_state"], "terminated")
        self.assertTrue(self.session["cleanup_verified"])
        self.assertFalse(self.session["uncertain_resource_fence"])

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
        self.provider.pods["fake1"]["status"] = "RUNNING"
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

    def test_startup_and_heartbeat_deadlines(self):
        self.session["created_at"] = 10
        self.tick()
        self.now = 311
        self.tick()
        self.assertEqual(self.session["error_code"], "startup_timeout")
        self.assertEqual(self.session["observed_state"], "terminated")

    def test_device_port_and_dashboard_origin(self):
        self.session["destination"] = "device"
        self.tick()
        pod = self.provider.pods["fake1"]
        self.assertEqual(pod["ports"], ["8780/http"])
        self.assertEqual(pod["gpu"]["minCudaVersion"], "12.8")
        self.assertEqual(pod["env"]["FM_DASHBOARD_ORIGIN"], "https://controller.example")

    def test_accepted_quote_survives_controller_delay(self):
        self.session["created_at"] = 11
        self.now = 131  # Accepted before expiry; provisioning occurs after expiry.
        self.tick()
        self.assertEqual(self.provider.creates, 1)
        self.assertEqual(self.session["observed_state"], "booting")

    def test_credential_failure_preserves_cleanup_fence(self):
        def unavailable(session):
            raise RuntimeError("private secret lookup error")
        self.lifecycle.provider_factory = unavailable
        self.session["create_attempted"] = True
        self.now = 1811
        self.tick()
        self.assertEqual(self.session["observed_state"], "terminating")
        self.assertEqual(self.session["error_code"], "credential_unavailable")
        self.assertFalse(self.session["cleanup_verified"])
        self.assertEqual(self.session["cleanup_status"], "credential_blocked")
        self.assertNotIn("private", str(self.session))

    def test_uncertain_create_escalates_but_keeps_watching(self):
        self.session["create_attempted"] = True
        self.now = 2111
        self.tick()
        self.assertTrue(self.session["manual_attention_required"])
        self.assertTrue(self.session["uncertain_resource_fence"])
        self.assertFalse(self.session["cleanup_verified"])
        self.assertEqual(self.session["observed_state"], "terminating")
        self.assertEqual(self.session["error_code"], "create_outcome_manual_attention")
        # A late resource is still found and deleted; no new create is attempted.
        self.provider.pods["latepod"] = {"id": "latepod", "name": "fm-session1", "status": "RUNNING"}
        self.tick()
        self.assertTrue(self.session["cleanup_verified"])
        self.assertFalse(self.session["manual_attention_required"])
        self.assertFalse(self.session["uncertain_resource_fence"])
        self.assertEqual(self.session["observed_state"], "terminated")
        self.assertEqual(self.provider.creates, 0)

    def test_malformed_provider_reads_fail_with_redacted_error(self):
        malformed = [None, [], {}, {"pods": None}, {"pods": ["secret"]},
            {"pods": [], "pagination": None}, {"pods": [], "pagination": {"hasNextPage": "false"}},
            {"pods": [{"id": "pod", "name": "fm-session1"}], "pagination": {"hasNextPage": True, "nextCursor": 7}}]
        for value in malformed:
            with self.subTest(value=value):
                provider = RunPod("secret-key", lambda *args: (200, value))
                with self.assertRaises(ProviderError):
                    provider.list_pods()
        for value in [None, [], {}, {"gpus": None}, {"gpus": ["secret"]},
                      {"gpus": [{"id": "gpu", "memory": "24", "price": {"secure": .5}}]},
                      {"gpus": [{"id": "gpu", "memory": 24, "price": {"secure": "secret"}}]}]:
            with self.subTest(value=value):
                with self.assertRaises(ProviderError):
                    RunPod("secret-key", lambda *args: (200, value)).gpus()

    def test_malformed_create_success_is_uncertain(self):
        for value in [None, {}, {"id": "pod"}]:
            with self.subTest(value=value):
                with self.assertRaises(CreateUncertain):
                    RunPod("secret-key", lambda *args: (201, value)).create_pod({"name": "fm-session1"})

    def test_malformed_provider_blocks_cleanup_instead_of_confirming(self):
        self.lifecycle.provider_factory = lambda s: RunPod("secret-key", lambda *args: (200, {"pods": []}))
        self.now = 1811
        self.tick()
        self.assertEqual(self.session["observed_state"], "terminating")
        self.assertFalse(self.session["cleanup_verified"])
        self.assertEqual(self.session["error_code"], "provider_unavailable")

    def test_worker_image_and_origin_validation(self):
        with self.assertRaises(ValueError):
            Lifecycle(None, worker_image="latest", control_plane_url="https://controller.example", persist_before_create=None, worker_environment=None)


if __name__ == "__main__":
    unittest.main()
