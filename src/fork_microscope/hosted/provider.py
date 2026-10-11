"""RunPod REST v2 controller adapter. No worker receives this credential.

Contract checked against https://api.runpod.io/v2/openapi.json 2026-10-01.
"""
import copy
import json
import math
import urllib.parse

from .credentials import Secret, UrllibTransport


_HTTP = UrllibTransport({"api.runpod.io"}, max_response_bytes=4 * 1024 * 1024)


class ProviderError(RuntimeError):
    def __init__(self, status=None):
        self.status = status
        super().__init__(f"RunPod request failed ({status or 'transport'})")


class CreateUncertain(ProviderError):
    """A create may have reached RunPod; never repeat it automatically."""


def http_transport(method, url, headers, body):
    """One bounded HTTPS request; credentials are never forwarded through redirects."""
    response = _HTTP.request(method, url, headers,
        None if body is None else json.dumps(body).encode(), timeout=30)
    # Ignore error/redirect payloads, which may contain reflected request secrets.
    return response.status, response.json() if 200 <= response.status < 300 else None


class RunPod:
    def __init__(self, api_key, transport=http_transport):
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("RunPod credential required")
        self._key = Secret(api_key)
        self.transport = transport

    def _headers(self):
        return {"Authorization": "Bearer " + self._key.reveal(), "Content-Type": "application/json",
                "User-Agent": "ForkMicroscope/0.1 (+https://github.com/iPolluxx/fork-microscope-workbench)"}

    def _request(self, method, path, body=None):
        try:
            status, result = self.transport(method, "https://api.runpod.io/v2" + path,
                self._headers(), body)
        except Exception:
            raise (CreateUncertain() if method == "POST" else ProviderError()) from None
        if type(status) is not int:
            raise (CreateUncertain() if method == "POST" else ProviderError())
        if status == 404 and method in ("GET", "DELETE"):
            return None
        if not 200 <= status < 300:
            raise (CreateUncertain(status) if method == "POST" and status >= 500 else ProviderError(status))
        return result

    @staticmethod
    def _pod(value, *, uncertain=False):
        if (not isinstance(value, dict) or not isinstance(value.get("id"), str)
            or not value["id"] or not isinstance(value.get("name"), str)
            or not value["name"] or ("status" in value and not isinstance(value["status"], str))):
            raise CreateUncertain() if uncertain else ProviderError()
        return value

    def gpus(self):
        data = self._request("GET", "/catalog/gpus?include=AVAILABILITY&product=POD&cloud=SECURE&count=1")
        if not isinstance(data, dict) or not isinstance(data.get("gpus"), list):
            raise ProviderError()
        for gpu in data["gpus"]:
            if (not isinstance(gpu, dict) or not isinstance(gpu.get("id"), str) or not gpu["id"]
                or type(gpu.get("memory")) not in (int, float) or not math.isfinite(gpu["memory"])
                or gpu["memory"] < 0 or not isinstance(gpu.get("price"), dict)
                or ("secure" in gpu and not isinstance(gpu["secure"], bool))
                or ("availability" in gpu and not isinstance(gpu["availability"], str))):
                raise ProviderError()
            for rate in gpu["price"].values():
                if rate is not None and (type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0):
                    raise ProviderError()
        return data["gpus"]

    def list_pods(self):
        pods, cursor, seen = [], None, set()
        while True:
            path = "/pods?limit=1000" + ("&cursor=" + urllib.parse.quote(cursor, safe="") if cursor else "")
            page = self._request("GET", path)
            if not isinstance(page, dict) or not isinstance(page.get("pods"), list):
                raise ProviderError()
            pods.extend(self._pod(pod) for pod in page["pods"])
            pagination = page.get("pagination")
            if (not isinstance(pagination, dict) or type(pagination.get("hasNextPage")) is not bool
                or (pagination.get("nextCursor") is not None and not isinstance(pagination["nextCursor"], str))
                or len(pods) > 100000):
                raise ProviderError()
            if not pagination.get("hasNextPage"):
                return pods
            cursor = pagination.get("nextCursor")
            if not cursor or cursor in seen:
                raise ProviderError()
            seen.add(cursor)

    def get_pod(self, pod_id):
        pod = self._request("GET", "/pods/" + urllib.parse.quote(pod_id, safe=""))
        return None if pod is None else self._pod(pod)

    def create_pod(self, body):
        return self._pod(self._request("POST", "/pods", body), uncertain=True)

    def delete_pod(self, pod_id):
        self._request("DELETE", "/pods/" + urllib.parse.quote(pod_id, safe=""))

    def legacy_balance(self):
        """Optional read-only legacy API; GraphQL is due to retire in early 2027.

        REST v2 billing history is spending, not prepaid balance. Do not substitute it.
        Authorization is sent only in a header, never a query string. A failed read
        is deliberately independent of pod readiness and never represents a payment.
        """
        try:
            status, result = self.transport("POST", "https://api.runpod.io/graphql",
                self._headers(), {"query": "query { myself { clientBalance currentSpendPerHr } }"})
        except Exception:
            raise ProviderError() from None
        if type(status) is not int or not 200 <= status < 300:
            raise ProviderError(status if type(status) is int else None)
        if not isinstance(result, dict) or result.get("errors"):
            raise ProviderError()
        account = result.get("data", {}).get("myself") if isinstance(result.get("data"), dict) else None
        if not isinstance(account, dict):
            raise ProviderError()
        balance, spend = account.get("clientBalance"), account.get("currentSpendPerHr")
        if (type(balance) not in (int, float) or not math.isfinite(balance)
            or type(spend) not in (int, float) or not math.isfinite(spend) or spend < 0):
            raise ProviderError()
        # Balance can be zero or negative; preserve the provider's observed value.
        return {"amount_usd": balance, "spend_per_hour_usd": spend}


class FakeRunPod:
    """Explicit test adapter, never an implicit production fallback."""
    def __init__(self, gpus=None):
        self.pods = {}
        self.catalog = gpus or []
        self.creates = 0
        self.deletes = []
        self.uncertain_create = False
        self.delete_pending = False

    def gpus(self):
        return copy.deepcopy(self.catalog)

    def list_pods(self):
        return copy.deepcopy(list(self.pods.values()))

    def get_pod(self, pod_id):
        return copy.deepcopy(self.pods.get(pod_id))

    def create_pod(self, body):
        self.creates += 1
        pod = dict(copy.deepcopy(body), id=f"fake{self.creates}", status="PROVISIONING")
        self.pods[pod["id"]] = pod
        if self.uncertain_create:
            raise CreateUncertain()
        return copy.deepcopy(pod)

    def delete_pod(self, pod_id):
        self.deletes.append(pod_id)
        if not self.delete_pending:
            self.pods.pop(pod_id, None)
