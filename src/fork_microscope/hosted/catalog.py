"""Pinned model allowlist and short-lived, provider-backed compute estimates."""
import math
import uuid

MODELS = ({"id": "Qwen/Qwen2.5-1.5B-Instruct", "revision": "989aa7980e4cf806f80c7fef2b1adb7bc71aa306", "minimum_vram_gb": 16},)


class Catalog:
    def __init__(self, provider_factory, *, disk_usd_per_gb_hour, disk_rate_source,
                 disk_gb=30, maximum_seconds=3600, quote_ttl_seconds=120,
                 startup_allowance_seconds=300, export_reserve_seconds=120):
        if not disk_rate_source or not math.isfinite(disk_usd_per_gb_hour) or disk_usd_per_gb_hour < 0:
            raise ValueError("Explicit disk price and source required")
        self.provider_factory = provider_factory
        self.disk_rate = disk_usd_per_gb_hour
        self.disk_rate_source = disk_rate_source
        self.disk_gb = disk_gb
        self.maximum_seconds = maximum_seconds
        self.ttl = quote_ttl_seconds
        self.startup = startup_allowance_seconds
        self.export = export_reserve_seconds

    def models(self):
        return [dict(model) for model in MODELS]

    def quote(self, body, now):
        model = next((m for m in MODELS if m["id"] == body.get("model_id")), None)
        if model is None:
            raise ValueError("Unsupported model")
        duration = body.get("max_duration_seconds", 1800)
        if isinstance(duration, bool) or not isinstance(duration, int) or not self.startup + self.export < duration <= self.maximum_seconds:
            raise ValueError("Invalid session duration")
        candidates = []
        for gpu in self.provider_factory(body).gpus():
            rate = gpu.get("price", {}).get("secure")
            if (gpu.get("secure") and gpu.get("memory", 0) >= model["minimum_vram_gb"]
                and gpu.get("availability") in {"LOW", "MEDIUM", "HIGH"}
                and isinstance(rate, (float, int)) and math.isfinite(rate) and rate > 0):
                candidates.append(gpu)
        requested = body.get("gpu_id")
        if requested:
            candidates = [gpu for gpu in candidates if gpu["id"] == requested]
        if not candidates:
            raise ValueError("No available compatible GPU")
        gpu = min(candidates, key=lambda g: g["price"]["secure"])
        rate = gpu["price"]["secure"]
        return {"id": uuid.uuid4().hex, "model_id": model["id"], "model_revision": model["revision"], "revision": model["revision"],
            "gpu_id": gpu["id"], "gpu_count": 1, "cloud": "SECURE", "gpu_usd_per_hour": rate,
            "disk_gb": self.disk_gb, "disk_usd_per_gb_hour": self.disk_rate,
            "disk_rate_source": self.disk_rate_source, "quoted_at": now, "expires_at": now + self.ttl,
            "max_duration_seconds": duration, "startup_allowance_seconds": self.startup,
            "export_reserve_seconds": self.export,
            "estimated_usd": (rate + self.disk_gb * self.disk_rate) * duration / 3600,
            "availability": gpu["availability"], "price_is_estimate": True,
            "cost_notice": "Availability is not reserved. Billing and cleanup delays can exceed this estimate."}
