# LCF/backhaul/wrapper.py
"""
Adapter wrapper that records plan/apply events into a Collector.
Usage: wrapped = BackhaulAdapterWrapper(adapter, Collector(...))
It delegates all methods to the adapter but intercepts plan/create/apply calls.
"""

from __future__ import annotations
import time
import os
from typing import Any, Dict, Optional

from LCF.backhaul.collector import Collector

# simple redact implementation - extend as needed
SECRET_KEYS = {"secret", "password", "private_key", "access_key", "secret_key", "token"}


def redact_secrets(obj: Optional[Dict]) -> Dict:
    if not isinstance(obj, dict):
        return {}
    out = {}
    for k, v in obj.items():
        lk = k.lower()
        if lk in SECRET_KEYS:
            out[k] = "<REDACTED>"
        else:
            if isinstance(v, dict):
                out[k] = redact_secrets(v)
            else:
                try:
                    # small scalar only
                    out[k] = v
                except Exception:
                    out[k] = "<UNSERIALIZABLE>"
    return out


class BackhaulAdapterWrapper:
    def __init__(self, adapter: Any, collector: Collector):
        self._adapter = adapter
        self._collector = collector
        # attempt to detect provider name
        self.provider = getattr(adapter, "name", getattr(adapter, "__class__", type(adapter)).__name__).lower()

    def __getattr__(self, name):
        # delegate everything missing to underlying adapter
        return getattr(self._adapter, name)

    # expected adapter method signatures handled by wrapper:
    # - plan(logical_id, spec) -> {"plan_id":..., "diff":..., "summary": {...}, "workdir":...}
    # - apply_plan(plan_id) -> {"success":bool, "adapter_id":..., "output": "...", "duration":...}
    # - create_instance(name, image, size, region, plan_only=True) -> fallback for small ops

    def plan(self, logical_id: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        # call underlying adapter.plan()
        res = self._adapter.plan(logical_id, spec)
        try:
            plan_text = res.get("diff") or res.get("plan_text") or ""
            plan_summary = res.get("summary") or res.get("plan_summary") or {}
            plan_id = res.get("plan_id") or f"plan-fallback-{logical_id}-{int(time.time())}"
            event = {
                "logical_id": logical_id,
                "provider": self.provider,
                "spec": redact_secrets(spec),
                "plan_id": plan_id,
                "plan_text": plan_text,
                "plan_summary": plan_summary,
            }
            self._collector.record_plan(event)
            # return res unchanged for caller
        except Exception:
            # never fail the plan due to collector problems
            pass
        return res

    def apply_plan(self, plan_id: str, **kwargs) -> Dict[str, Any]:
        start = time.time()
        res = self._adapter.apply_plan(plan_id, **kwargs)
        try:
            duration = res.get("duration") or (time.time() - start)
            out = res.get("output") or res.get("stdout") or ""
            apply_event = {
                "success": bool(res.get("success")),
                "duration_s": float(duration),
                "output": out,
            }
            # try update collector; if no matching run exists, collector.record_apply will return False — that's okay.
            self._collector.record_apply(plan_id, apply_event)
        except Exception:
            pass
        return res

    # convenience wrapper for create_instance used by autoscaler
    def create_instance(self, name: str, image: str, size: str, region: str, plan_only: bool = True) -> Dict[str, Any]:
        # call underlying adapter's create_instance (adapters may accept different params)
        # Normalize result to include plan_id/diff/summary in plan-only case
        try:
            res = self._adapter.create_instance(name, image, size, region, plan_only=plan_only)
        except TypeError:
            # fallback: some adapters expect (name, spec, plan_only)
            try:
                res = self._adapter.create_instance(name, {"image": image, "size": size, "region": region}, plan_only=plan_only)
            except Exception as e:
                return {"success": False, "error": str(e)}
        # If plan-only, record plan
        if plan_only:
            try:
                plan_id = res.get("plan_id") or f"plan-fallback-{name}-{int(time.time())}"
                plan_text = res.get("diff") or ""
                plan_summary = res.get("summary") or {}
                self._collector.record_plan({
                    "logical_id": name,
                    "provider": self.provider,
                    "spec": redact_secrets({"image": image, "size": size, "region": region}),
                    "plan_id": plan_id,
                    "plan_text": plan_text,
                    "plan_summary": plan_summary
                })
            except Exception:
                pass
        else:
            # apply: try to record apply by plan_id if provided, otherwise synthesize
            try:
                plan_id = res.get("plan_id") or f"plan-fallback-{name}-{int(time.time())}"
                self._collector.record_apply(plan_id, {"success": bool(res.get("success")), "duration_s": res.get("duration") or 0.0, "output": res.get("output") or ""})
            except Exception:
                pass
        return res
