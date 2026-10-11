"""Safe BYOK onboarding/account observations; never funds an account or rents compute.

The public key form can call ``validate_runpod_key`` before writing its vault entry.
Only the private controller may construct ``RunPodAccount`` from a vault-backed
provider factory. Owner identity is supplied by authenticated server context.
"""
import math

from .credentials import OWNER
from .provider import ProviderError, RunPod


BILLING_URL = "https://console.runpod.io/billing"
_MESSAGES = {
    "ready": "Catalog and pod reads verified. Pod write permissions remain unverified.",
    "invalid_key": "RunPod did not accept this credential.",
    "permission_denied": "RunPod denied a required catalog or pod read permission.",
    "unavailable": "RunPod access could not be verified. Try again before connecting.",
}


def _capabilities():
    return {"catalog_read": "unverified", "pods_read": "unverified", "pods_write": "unverified"}


def _failure(exc):
    status = exc.status if isinstance(exc, ProviderError) else None
    return "invalid_key" if status == 401 else "permission_denied" if status == 403 else "unavailable"


def _readiness(provider):
    capabilities = _capabilities()
    try:
        provider.gpus()
        capabilities["catalog_read"] = "verified"
        provider.list_pods()
        capabilities["pods_read"] = "verified"
    except Exception as exc:
        status = _failure(exc)
    else:
        status = "ready"
    return {"valid": status == "ready", "status": status,
            "capabilities": capabilities, "message": _MESSAGES[status]}


def validate_runpod_key(value, *, provider_builder=RunPod):
    """Validate required reads only, with injectable transport/client for fake tests.

    No create/delete permission is inferred from successful reads. Do not persist
    the supplied key unless the returned ``valid`` field is exactly ``True``.
    Response and exception text from the provider is never returned or logged.
    """
    if (not isinstance(value, str) or not 10 <= len(value) <= 1024
        or not value.isascii() or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        return {"valid": False, "status": "invalid_key", "capabilities": _capabilities(),
                "message": _MESSAGES["invalid_key"]}
    try:
        provider = provider_builder(value)
    except Exception as exc:
        status = _failure(exc)
        return {"valid": False, "status": status, "capabilities": _capabilities(),
                "message": _MESSAGES[status]}
    return _readiness(provider)


def _balance(provider=None, *, unavailable="unavailable", enabled=True):
    result = {"status": unavailable, "amount_usd": None, "spend_per_hour_usd": None,
              "source": "runpod_graphql_legacy", "deprecated": True}
    if not enabled:
        result["status"] = "disabled"
        return result
    if provider is None:
        return result
    read = getattr(provider, "legacy_balance", None)
    if not callable(read):
        result["status"] = "unsupported"
        return result
    try:
        value = read()
        amount, spend = value["amount_usd"], value["spend_per_hour_usd"]
        if (type(amount) not in (int, float) or not math.isfinite(amount)
            or type(spend) not in (int, float) or not math.isfinite(spend) or spend < 0):
            raise ValueError()
    except Exception as exc:
        result["status"] = _failure(exc)
        if isinstance(exc, ProviderError) and exc.status in (404, 410):
            result["status"] = "unsupported"
    else:
        result.update(status="available", amount_usd=amount, spend_per_hour_usd=spend)
    return result


class RunPodAccount:
    def __init__(self, provider_factory, *, legacy_balance_enabled=True):
        self.provider_factory = provider_factory
        self.legacy_balance_enabled = legacy_balance_enabled

    def status(self, owner_uid):
        """Return sanitized observations; the factory alone resolves owner-bound secrets.

        A missing/unreadable credential yields ``connected=None`` rather than a
        false claim that an existing connection was deleted. The calling service
        can report disconnected from its trusted connection record before calling.
        """
        result = {"connected": None, "readiness": "credential_unavailable", "capabilities": _capabilities(),
                  "balance": _balance(enabled=self.legacy_balance_enabled), "billing_url": BILLING_URL,
                  "write_permissions_verified": False}
        if not isinstance(owner_uid, str) or not OWNER.fullmatch(owner_uid):
            return result
        try:
            provider = self.provider_factory({"owner_uid": owner_uid})
        except Exception:
            return result
        readiness = _readiness(provider)
        result.update(connected=True, readiness=readiness["status"], capabilities=readiness["capabilities"])
        # Avoid another request on a known rejected credential. Balance is optional
        # and a denial here must never invalidate otherwise usable pod access.
        if readiness["valid"]:
            result["balance"] = _balance(provider, enabled=self.legacy_balance_enabled)
        return result


def billing_handoff(amount_usd=None):
    """Return official user-completed checkout; amount is intent, never a charge.

    The URL intentionally has no amount, payment or API-key query parameters.
    No supported public add-credit API has been established.
    """
    if amount_usd is not None and (type(amount_usd) not in (int, float)
        or not math.isfinite(amount_usd) or amount_usd <= 0):
        raise ValueError("Funding amount must be a positive USD amount.")
    return {"url": BILLING_URL, "requested_amount_usd": amount_usd, "payment_performed": False,
            "requires_user_payment": True,
            "message": "Complete any payment in RunPod Billing, then refresh account status. A balance change does not confirm a particular payment."}
