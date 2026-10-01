"""RunPod REST v2 controller adapter. No worker receives this credential.

Contract checked against https://api.runpod.io/v2/openapi.json 2026-10-01.
"""
import copy
import json
import urllib.error
import urllib.parse
import urllib.request


class ProviderError(RuntimeError):
    def __init__(self, status=None):
        self.status = status
        super().__init__(f"RunPod request failed ({status or 'transport'})")


class CreateUncertain(ProviderError):
    """A create may have reached RunPod; never repeat it automatically."""


def http_transport(method, url, headers, body):
    request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                     headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                raise ProviderError()
            return response.status, json.loads(data) if data else None
    except urllib.error.HTTPError as error:
        # Deliberately do not echo server detail, request bodies, or credentials.
        return error.code, None


class RunPod:
    def __init__(self, api_key, transport=http_transport):
        if not api_key:
            raise ValueError("RunPod credential required")
        self._key = api_key
        self.transport = transport

    def _request(self, method, path, body=None):
        try:
            status, result = self.transport(method, "https://api.runpod.io/v2" + path,
                {"Authorization": "Bearer " + self._key, "Content-Type": "application/json"}, body)
        except Exception:
            raise (CreateUncertain() if method == "POST" else ProviderError()) from None
        if status == 404 and method in ("GET", "DELETE"):
            return None
        if not 200 <= status < 300:
            raise (CreateUncertain(status) if method == "POST" and status >= 500 else ProviderError(status))
        return result

    def gpus(self):
        data = self._request("GET", "/catalog/gpus?include=AVAILABILITY&product=POD&cloud=SECURE&count=1")
        return data["gpus"]

    def list_pods(self):
        pods, cursor, seen = [], None, set()
        while True:
            path = "/pods?limit=1000" + ("&cursor=" + urllib.parse.quote(cursor, safe="") if cursor else "")
            page = self._request("GET", path)
            pods.extend(page["pods"])
            pagination = page.get("pagination", {})
            if not pagination.get("hasNextPage"):
                return pods
            cursor = pagination.get("nextCursor")
            if not cursor or cursor in seen:
                raise ProviderError()
            seen.add(cursor)

    def get_pod(self, pod_id):
        return self._request("GET", "/pods/" + urllib.parse.quote(pod_id, safe=""))

    def create_pod(self, body):
        return self._request("POST", "/pods", body)

    def delete_pod(self, pod_id):
        self._request("DELETE", "/pods/" + urllib.parse.quote(pod_id, safe=""))


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
        pod = dict(copy.deepcopy(body), id=f"fake-{self.creates}", status="PROVISIONING")
        self.pods[pod["id"]] = pod
        if self.uncertain_create:
            raise CreateUncertain()
        return copy.deepcopy(pod)

    def delete_pod(self, pod_id):
        self.deletes.append(pod_id)
        if not self.delete_pending:
            self.pods.pop(pod_id, None)
