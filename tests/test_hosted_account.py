"""BYOK account contract exercised only through injected fake transports."""
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from fork_microscope.hosted import provider as provider_module
from fork_microscope.hosted.account import BILLING_URL, RunPodAccount, billing_handoff, validate_runpod_key
from fork_microscope.hosted.credentials import TransportError, UrllibTransport, _NoRedirect
from fork_microscope.hosted.provider import ProviderError, RunPod


KEY = "fake-secret-key-for-tests-only"
EMPTY_PODS = {"pods": [], "pagination": {"hasNextPage": False}}


class AccountFixture:
    def __init__(self, *, catalog=(200, {"gpus": []}), pods=(200, EMPTY_PODS), balance=None):
        self.catalog, self.pods = catalog, pods
        self.balance = balance if balance is not None else (200, {"data": {"myself": {"clientBalance": 0, "currentSpendPerHr": 0}}})
        self.calls = []

    def transport(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if "/catalog/gpus" in url:
            result = self.catalog
        elif url.endswith("/pods?limit=1000"):
            result = self.pods
        elif url == "https://api.runpod.io/graphql":
            result = self.balance
        else:
            raise AssertionError("Unexpected or mutating provider request")
        if isinstance(result, Exception):
            raise result
        return result

    def provider(self, key=KEY):
        return RunPod(key, self.transport)


class HostedAccountTests(unittest.TestCase):
    def test_required_reads_and_unverified_writes(self):
        fixture = AccountFixture()
        result = validate_runpod_key(KEY, provider_builder=fixture.provider)
        self.assertTrue(result["valid"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["capabilities"], {"catalog_read": "verified", "pods_read": "verified", "pods_write": "unverified"})
        self.assertEqual([call[0] for call in fixture.calls], ["GET", "GET"])
        self.assertNotIn(KEY, json.dumps(result))

    def test_input_key_syntax_fails_before_provider(self):
        def forbidden(key):
            self.fail("Invalid key must not reach transport")
        for value in (None, 123, "short", " " * 20, KEY + "\n", "é" * 20, "x" * 1025):
            with self.subTest(value=repr(value)):
                result = validate_runpod_key(value, provider_builder=forbidden)
                self.assertFalse(result["valid"])
                self.assertEqual(result["status"], "invalid_key")

    def test_rejected_credentials_and_read_permissions_are_distinct(self):
        for status, expected in ((401, "invalid_key"), (403, "permission_denied"), (302, "unavailable"), (500, "unavailable")):
            with self.subTest(status=status):
                fixture = AccountFixture(catalog=(status, {"message": KEY}))
                result = validate_runpod_key(KEY, provider_builder=fixture.provider)
                self.assertEqual(result["status"], expected)
                self.assertFalse(result["valid"])
                self.assertEqual(len(fixture.calls), 1)
                self.assertNotIn(KEY, json.dumps(result))

    def test_catalog_alone_does_not_prove_pod_access(self):
        fixture = AccountFixture(pods=(403, {"message": KEY}))
        result = validate_runpod_key(KEY, provider_builder=fixture.provider)
        self.assertFalse(result["valid"])
        self.assertEqual(result["capabilities"]["catalog_read"], "verified")
        self.assertEqual(result["capabilities"]["pods_read"], "unverified")

    def test_timeout_and_malformed_provider_data_do_not_connect(self):
        for value in (TimeoutError(KEY), (200, {"gpus": KEY}), (200, {"gpus": [{"id": KEY}]})):
            fixture = AccountFixture(catalog=value)
            result = validate_runpod_key(KEY, provider_builder=fixture.provider)
            self.assertEqual(result["status"], "unavailable")
            self.assertNotIn(KEY, json.dumps(result))
        fixture = AccountFixture(pods=(200, {"pods": [{"id": "id", "name": KEY}]}))
        self.assertFalse(validate_runpod_key(KEY, provider_builder=fixture.provider)["valid"])

    def test_account_identity_is_passed_only_to_owner_bound_factory(self):
        fixture, subjects = AccountFixture(), []
        def factory(subject):
            subjects.append(subject)
            return fixture.provider()
        result = RunPodAccount(factory).status("user-1")
        self.assertEqual(subjects, [{"owner_uid": "user-1"}])
        self.assertTrue(result["connected"])
        self.assertEqual(result["readiness"], "ready")
        self.assertFalse(result["write_permissions_verified"])
        self.assertNotIn(KEY, json.dumps(result))

    def test_invalid_owner_does_not_resolve_a_secret(self):
        def forbidden(subject):
            self.fail("Invalid UID must not reach vault-backed factory")
        result = RunPodAccount(forbidden).status("../another-user")
        self.assertIsNone(result["connected"])
        self.assertEqual(result["readiness"], "credential_unavailable")

    def test_factory_failure_is_generic_and_not_false_disconnected_claim(self):
        def unavailable(subject):
            raise RuntimeError(KEY)
        result = RunPodAccount(unavailable).status("u")
        self.assertIsNone(result["connected"])
        self.assertEqual(result["readiness"], "credential_unavailable")
        self.assertNotIn(KEY, json.dumps(result))

    def test_zero_balance_is_available_and_header_only(self):
        fixture = AccountFixture()
        result = RunPodAccount(lambda subject: fixture.provider()).status("u")
        self.assertEqual(result["balance"], {"status": "available", "amount_usd": 0,
            "spend_per_hour_usd": 0, "source": "runpod_graphql_legacy", "deprecated": True})
        call = fixture.calls[-1]
        self.assertEqual(call[0:2], ("POST", "https://api.runpod.io/graphql"))
        self.assertEqual(call[2]["Authorization"], "Bearer " + KEY)
        self.assertEqual(call[3], {"query": "query { myself { clientBalance currentSpendPerHr } }"})
        self.assertNotIn(KEY, call[1])
        self.assertNotIn(KEY, json.dumps(call[3]))

    def test_optional_balance_denial_does_not_remove_pod_readiness(self):
        for status, expected in ((401, "invalid_key"), (403, "permission_denied"), (404, "unsupported"), (410, "unsupported"), (302, "unavailable"), (503, "unavailable")):
            fixture = AccountFixture(balance=(status, {"message": KEY}))
            result = RunPodAccount(lambda subject: fixture.provider()).status("u")
            self.assertEqual(result["readiness"], "ready")
            self.assertEqual(result["balance"]["status"], expected)
            self.assertIsNone(result["balance"]["amount_usd"])
            self.assertNotIn(KEY, json.dumps(result))

    def test_balance_malformed_errors_are_never_echoed(self):
        invalid = [None, {}, {"data": []}, {"data": {"myself": {"clientBalance": KEY}}},
            {"errors": [{"message": KEY}], "data": {"myself": {"clientBalance": 12, "currentSpendPerHr": .1}}}]
        for balance in (True, float("inf"), float("nan"), "3.0"):
            invalid.append({"data": {"myself": {"clientBalance": balance, "currentSpendPerHr": 0}}})
        for value in invalid:
            fixture = AccountFixture(balance=(200, value))
            result = RunPodAccount(lambda subject: fixture.provider()).status("u")
            self.assertEqual(result["balance"]["status"], "unavailable")
            self.assertIsNone(result["balance"]["amount_usd"])
            self.assertNotIn(KEY, json.dumps(result))

    def test_negative_balance_is_observed_without_funding_claim(self):
        fixture = AccountFixture(balance=(200, {"data": {"myself": {"clientBalance": -1.2, "currentSpendPerHr": .45}}}))
        result = RunPodAccount(lambda subject: fixture.provider()).status("u")
        self.assertEqual(result["balance"]["amount_usd"], -1.2)
        self.assertEqual(result["balance"]["spend_per_hour_usd"], .45)
        self.assertNotIn("payment_performed", result)

    def test_legacy_balance_can_be_disabled_without_request(self):
        fixture = AccountFixture()
        result = RunPodAccount(lambda subject: fixture.provider(), legacy_balance_enabled=False).status("u")
        self.assertEqual(result["balance"]["status"], "disabled")
        self.assertEqual(len(fixture.calls), 2)

    def test_revoked_key_skips_balance_probe(self):
        fixture = AccountFixture(catalog=(401, {"message": KEY}))
        result = RunPodAccount(lambda subject: fixture.provider()).status("u")
        self.assertTrue(result["connected"])
        self.assertEqual(result["readiness"], "invalid_key")
        self.assertEqual(len(fixture.calls), 1)

    def test_funding_is_user_completed_handoff_only(self):
        for amount in (None, 5, 20.25):
            result = billing_handoff(amount)
            self.assertEqual(result["url"], BILLING_URL)
            self.assertEqual(result["requested_amount_usd"], amount)
            self.assertFalse(result["payment_performed"])
            self.assertTrue(result["requires_user_payment"])
            self.assertNotIn("?", result["url"])
        for amount in (True, "20", 0, -5, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                billing_handoff(amount)

    def test_provider_credential_repr_and_exceptions_are_safe(self):
        fixture = AccountFixture(balance=TimeoutError(KEY))
        client = fixture.provider()
        self.assertNotIn(KEY, repr(vars(client)))
        with self.assertRaises(ProviderError) as error:
            client.legacy_balance()
        self.assertNotIn(KEY, str(error.exception))
        self.assertIsNone(error.exception.__cause__)


class ProviderTransportSafetyTests(unittest.TestCase):
    def test_redirect_handler_is_installed_and_refuses_redirect(self):
        transport = UrllibTransport({"api.runpod.io"})
        handlers = [h for h in transport._opener.handlers if isinstance(h, _NoRedirect)]
        self.assertEqual(len(handlers), 1)
        self.assertIsNone(handlers[0].redirect_request(None, None, None, None, None, None))

    def test_redirect_payload_is_not_parsed_or_forwarded(self):
        class Opener:
            def __init__(self):
                self.calls = []
            def open(self, request, timeout):
                self.calls.append(request)
                raise urllib.error.HTTPError(request.full_url, 302, "Found",
                    {"Location": "https://attacker.invalid/?key=" + KEY}, io.BytesIO(KEY.encode()))
        opener = Opener()
        transport = UrllibTransport({"api.runpod.io"}, opener=opener)
        with patch.object(provider_module, "_HTTP", transport):
            result = validate_runpod_key(KEY)
        self.assertFalse(result["valid"])
        self.assertEqual(len(opener.calls), 1)
        self.assertNotIn(KEY, json.dumps(result))

    def test_disallowed_origin_is_rejected_before_opener(self):
        class Opener:
            def open(self, *args, **kwargs):
                raise AssertionError("Must reject origin first")
        transport = UrllibTransport({"api.runpod.io"}, opener=Opener())
        for url in ("http://api.runpod.io/v2/pods", "https://attacker.invalid/pods", "https://key@api.runpod.io/v2/pods"):
            with self.assertRaises(TransportError):
                transport.request("GET", url, {"Authorization": "Bearer " + KEY})

    def test_oversized_and_invalid_json_success_responses_are_generic(self):
        class Response(io.BytesIO):
            status = 200
            headers = {}
        class Opener:
            def __init__(self, value):
                self.value = value
            def open(self, request, timeout):
                return Response(self.value)
        for payload, limit in ((KEY.encode(), 8), (KEY.encode(), 1024)):
            with patch.object(provider_module, "_HTTP", UrllibTransport({"api.runpod.io"}, max_response_bytes=limit, opener=Opener(payload))):
                result = validate_runpod_key(KEY)
            self.assertFalse(result["valid"])
            self.assertEqual(result["status"], "unavailable")
            self.assertNotIn(KEY, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
