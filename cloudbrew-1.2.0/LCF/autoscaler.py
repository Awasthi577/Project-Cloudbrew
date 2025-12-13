# LCF/autoscaler.py
"""
Autoscaler core for CloudBrew.

Provides:
- parse_autoscale_string(s)
- AutoscalerManager.run_once(logical_prefix, spec, autoscale_cfg, observed, plan_only)
- AutoscalerManager.run_loop(specs, interval_seconds, stop_event)
"""
from __future__ import annotations
import time
import threading
import json
import re
import math
from typing import Dict, Any, Optional, List

from LCF import store
from LCF.cloud_adapters import get_compute_adapter
from LCF.cloud_adapters.noop_adapter import NoopComputeAdapter

DEFAULT_COOLDOWN = 60


def parse_autoscale_string(s: Optional[str]) -> Dict[str, Any]:
    """
    Parse compact autoscale string formats like:
      "1:5@cpu:70,60"  -> min=1,max=5, policy metric=cpu threshold=70, cooldown=60
      "3" -> min=max=3
      JSON string -> parsed
    """
    if not s or s.strip() == "":
        return {"min": 1, "max": 1, "policy": [], "cooldown": DEFAULT_COOLDOWN}

    s = s.strip()
    if s[0] in ("{", "["):
        try:
            return json.loads(s)
        except Exception:
            raise ValueError("Invalid autoscale JSON string")

    # basic pattern: "<min>:<max>@<metric>:<threshold>,<cooldown>"
    m = re.match(r"^\s*(\d+)\s*:\s*(\d+)(?:\s*@\s*([a-zA-Z0-9_]+)\s*:\s*(\d+)\s*,\s*(\d+))?\s*$", s)
    if m:
        min_c = int(m.group(1))
        max_c = int(m.group(2))
        metric = m.group(3)
        threshold = int(m.group(4)) if m.group(4) else None
        cooldown = int(m.group(5)) if m.group(5) else DEFAULT_COOLDOWN
        policy = []
        if metric and threshold is not None:
            policy.append({"type": "threshold", "metric": metric, "threshold": threshold})
        return {"min": min_c, "max": max_c, "policy": policy, "cooldown": cooldown}

    if s.isdigit():
        n = int(s)
        return {"min": n, "max": n, "policy": [], "cooldown": DEFAULT_COOLDOWN}

    raise ValueError(f"Unsupported autoscale string: {s}")


class AutoscalerManager:
    def __init__(self, db_path: Optional[str] = None, provider: str = "noop", adapter=None):
        self.store = store.SQLiteStore(db_path)
        if adapter:
            self.adapter = adapter
        else:
            try:
                self.adapter = get_compute_adapter(provider or "noop")
            except Exception:
                self.adapter = NoopComputeAdapter()
        self._cooldowns: Dict[str, int] = {}  # logical_prefix -> last_action_ts

    def _in_cooldown(self, logical_prefix: str, cooldown: int) -> bool:
        last = self._cooldowns.get(logical_prefix, 0)
        return (time.time() - last) < cooldown

    def _set_cooldown(self, logical_prefix: str):
        self._cooldowns[logical_prefix] = int(time.time())

    def _evaluate_desired(self, current_count: int, autoscale_cfg: Dict[str, Any], observed: Dict[str, Any]) -> int:
        """
        Simple evaluator:
        - if no policy: desired = clamp(current_count, min, max)
        - supports threshold policy to increment by 1 when metric > threshold
        """
        min_c = int(autoscale_cfg.get("min", 1))
        max_c = int(autoscale_cfg.get("max", min_c))
        policies = autoscale_cfg.get("policy", [])
        desired = current_count

        if not policies:
            return max(min_c, min(max_c, current_count))

        for p in policies:
            ptype = p.get("type", "threshold")
            if ptype == "threshold":
                metric = p.get("metric")
                threshold = p.get("threshold")
                if metric and threshold is not None:
                    val = observed.get(metric, 0)
                    if val > threshold:
                        desired = min(max_c, desired + 1)
            elif ptype == "step":
                metric = p.get("metric")
                threshold = p.get("threshold")
                delta = int(p.get("delta", 1))
                if metric and threshold is not None:
                    val = observed.get(metric, 0)
                    if val > threshold:
                        desired = min(max_c, desired + delta)
            elif ptype == "target_utilization":
                metric = p.get("metric")
                target = float(p.get("target", 0) or 0)
                if metric and target > 0:
                    cur_val = float(observed.get(metric, 0) or 0)
                    if cur_val > 0 and current_count > 0:
                        desired = int(max(min_c, min(max_c, math.ceil((cur_val / target) * float(current_count)))))
        return max(min_c, min(max_c, desired))

    def _safe_create(self, logical_id: str, spec: Dict[str, Any], plan_only: bool = True) -> Dict[str, Any]:
        try:
            return self.adapter.create_instance(logical_id, spec, plan_only=plan_only)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _safe_delete(self, adapter_id: str) -> Dict[str, Any]:
        try:
            ok = self.adapter.delete_instance(adapter_id)
            return {"success": bool(ok)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def run_once(self, logical_prefix: str, spec: Dict[str, Any], autoscale_cfg: Dict[str, Any], observed_metrics: Dict[str, Any], plan_only: bool = True) -> Dict[str, Any]:
        """
        Reconcile once:
        - read store for existing children matching logical_prefix
        - compute desired using policies
        - create or delete to converge
        - persist actions to store via store.log_action / upsert_instance
        """
        # current instances that match prefix
        rows = self.store.list_instances()
        current_instances = [r for r in rows if r["logical_id"].startswith(logical_prefix)]
        current_count = len(current_instances)

        desired = self._evaluate_desired(current_count, autoscale_cfg, observed_metrics)

        # respect cooldown
        cooldown = int(autoscale_cfg.get("cooldown", DEFAULT_COOLDOWN))
        if self._in_cooldown(logical_prefix, cooldown):
            # do nothing, but return report
            return {"logical_id": logical_prefix, "desired": desired, "actual": current_count, "actions": [], "actual_after": current_count, "cooldown": True}

        actions = []
        stamp = int(time.time())

        # scale up
        if desired > current_count:
            to_add = desired - current_count
            for i in range(to_add):
                child_logical = f"{logical_prefix}-{stamp}-{i}"
                child_spec = dict(spec)
                child_spec["name"] = child_logical
                res = self._safe_create(child_logical, child_spec, plan_only=plan_only)
                # persist if applied and adapter returned adapter_id
                if not plan_only and res.get("success"):
                    adapter_id = res.get("adapter_id") or res.get("InstanceId") or f"{self.adapter.__class__.__name__}-{child_logical}"
                    inst = {
                        "logical_id": child_logical,
                        "adapter": getattr(self.adapter, "__class__", type(self.adapter)).__name__.lower(),
                        "adapter_id": adapter_id,
                        "spec": child_spec,
                        "state": "running",
                        "created_at": stamp
                    }
                    try:
                        self.store.upsert_instance(inst)
                    except Exception:
                        pass
                actions.append({"action": "create", "logical_id": child_logical, "res": res})

        # scale down
        elif desired < current_count:
            to_remove = current_count - desired
            removable = sorted(current_instances, key=lambda r: r.get("created_at", 0))[:to_remove]
            for r in removable:
                adapter_id = r.get("adapter_id")
                res = self._safe_delete(adapter_id)
                if res.get("success"):
                    try:
                        self.store.delete_instance_by_adapter_id(adapter_id)
                    except Exception:
                        pass
                actions.append({"action": "delete", "logical_id": r["logical_id"], "adapter_id": adapter_id, "res": res})

        # log to actions table
        for a in actions:
            try:
                self.store.log_action(a.get("action"), {"logical_id": a.get("logical_id"), "res": a.get("res"), "ts": int(time.time())})
            except Exception:
                pass

        # set cooldown if we took actions
        if actions:
            self._set_cooldown(logical_prefix)

        actual_after = len([r for r in self.store.list_instances() if r["logical_id"].startswith(logical_prefix)]) if not plan_only else max(current_count, desired)
        return {"logical_id": logical_prefix, "desired": desired, "actual": current_count, "actions": actions, "actual_after": actual_after, "cooldown": False}

    def run_loop(self, specs: List[Dict[str, Any]], interval_seconds: int = 30, stop_event: Optional[threading.Event] = None):
        """
        Run reconcile loop. Each spec item: {"logical_prefix":..., "spec":..., "autoscale_cfg":..., "observed_metrics":...}
        """
        if stop_event is None:
            stop_event = threading.Event()

        while not stop_event.is_set():
            for s in specs:
                lp = s.get("logical_prefix") or s.get("name") or (s.get("spec") or {}).get("name")
                if not lp:
                    continue
                cfg = s.get("autoscale_cfg") or {"min": s.get("spec", {}).get("count", 1), "max": s.get("spec", {}).get("count", 1), "policy": [], "cooldown": DEFAULT_COOLDOWN}
                observed = s.get("observed_metrics") or {}
                try:
                    self.run_once(lp, s.get("spec", {}), cfg, observed, plan_only=True)
                except Exception:
                    try:
                        self.store.log_action("autoscaler.loop.error", {"logical_prefix": lp, "ts": int(time.time())})
                    except Exception:
                        pass
            stop_event.wait(interval_seconds)
