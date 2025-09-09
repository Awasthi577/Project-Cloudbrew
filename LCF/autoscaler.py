# LCF/autoscaler.py
import re
import json
import time
import threading
from typing import Dict, Any, Optional
from LCF import store
from LCF.cloud_adapters import get_compute_adapter

AUTOSCALE_RE = re.compile(r"^(?P<min>\d+):(?P<max>\d+)@(?P<metric>\w+):(?P<thr>\d+),(?P<cooldown>\d+)$")

def parse_autoscale_string(s: str) -> Dict[str, Any]:
    if not s:
        raise ValueError("autoscale string is empty")
    s = s.strip()
    m = AUTOSCALE_RE.match(s)
    if m:
        return {
            "min": int(m.group("min")),
            "max": int(m.group("max")),
            "policy": [{"metric": m.group("metric"), "threshold": int(m.group("thr"))}],
            "cooldown": int(m.group("cooldown"))
        }
    try:
        parsed = json.loads(s)
        if "min" not in parsed or "max" not in parsed:
            raise ValueError("JSON autoscale must contain 'min' and 'max'")
        parsed.setdefault("policy", [])
        parsed.setdefault("cooldown", 60)
        return parsed
    except json.JSONDecodeError:
        raise ValueError(f"Invalid autoscale string: {s!r}. Expected 'min:max@metric:threshold,cooldown' or JSON.")

class AutoscalerManager:
    """
    Minimal autoscaler: persists to SQLite, uses adapter registry, supports cooldown/backoff.
    Default DB path is cloudbrew.db for persistence.
    """
    def __init__(self, db_path: Optional[str] = "cloudbrew.db", provider: str = "noop"):
        self.store = store.SQLiteStore(db_path)
        self.provider = provider
        self.adapter = get_compute_adapter(provider)
        self._stop_event = threading.Event()
        self._last_action_ts: Dict[str, int] = {}

    def _count_actual(self, logical_id_prefix: str) -> int:
        return self.store.count_instances(logical_id_prefix)

    def decide_desired(self, observed_metrics: Dict[str, float], autoscale_cfg: Dict[str, Any]) -> int:
        desired = int(autoscale_cfg.get("min", 1))
        maxr = int(autoscale_cfg.get("max", desired))
        policy = autoscale_cfg.get("policy", [])
        for p in policy:
            metric = p.get("metric")
            thr = float(p.get("threshold", 0))
            cur = float(observed_metrics.get(metric, 0))
            if cur > thr:
                desired = min(maxr, desired + 1)
        return desired

    def reconcile(self, logical_id: str, spec: Dict[str, Any], desired: int, plan_only: bool = False) -> Dict[str, Any]:
        prefix = logical_id
        actual_before = self._count_actual(prefix)
        result = {"logical_id": logical_id, "desired": desired, "actual": actual_before, "actions": []}
        ts_now = int(time.time())
        cooldown = spec.get("_autoscale_cfg", {}).get("cooldown", 60)
        last_ts = self._last_action_ts.get(logical_id, 0)
        if ts_now - last_ts < cooldown:
            result["note"] = f"in cooldown (last action {ts_now - last_ts}s ago, cooldown={cooldown}s)"
            result["actual_after"] = actual_before
            return result

        if desired > actual_before:
            to_create = desired - actual_before
            for i in range(to_create):
                lid = f"{logical_id}-{int(time.time())}-{i}"
                try:
                    res = self.adapter.create_instance(lid, spec, plan_only)
                    adapter_id = None
                    if isinstance(res, dict):
                        adapter_id = res.get("adapter_id") or res.get("InstanceId")
                    inst = {
                        "logical_id": lid,
                        "adapter": getattr(self.adapter, "__class__").__name__.lower(),
                        "adapter_id": adapter_id or f"fake-{lid}",
                        "spec": spec,
                        "state": "running",
                        "created_at": int(time.time())
                    }
                    if not plan_only:
                        self.store.upsert_instance(inst)
                    result["actions"].append({"action": "create", "logical_id": lid, "res": res})
                except TypeError:
                    name = lid
                    image = spec.get("image")
                    size = spec.get("size")
                    region = spec.get("region", "local")
                    res = self.adapter.create_instance(name=name, image=image, size=size, region=region)
                    adapter_id = None
                    if isinstance(res, dict):
                        adapter_id = res.get("InstanceId")
                    inst = {
                        "logical_id": lid,
                        "adapter": getattr(self.adapter, "__class__").__name__.lower(),
                        "adapter_id": adapter_id or f"fake-{lid}",
                        "spec": spec,
                        "state": "running",
                        "created_at": int(time.time())
                    }
                    if not plan_only:
                        self.store.upsert_instance(inst)
                    result["actions"].append({"action": "create", "logical_id": lid, "res": res})
        elif desired < actual_before:
            to_remove = actual_before - desired
            rows = self.store.list_instances_by_prefix(prefix)
            rows_sorted = sorted(rows, key=lambda r: r.get("created_at", 0), reverse=True)
            for r in rows_sorted[:to_remove]:
                adapter_id = r.get("adapter_id")
                try:
                    ok = self.adapter.destroy_instance(adapter_id)
                except TypeError:
                    ok = self.adapter.delete_instance(adapter_id)
                if ok:
                    self.store.delete_instance_by_adapter_id(adapter_id)
                result["actions"].append({"action": "destroy", "adapter_id": adapter_id, "ok": bool(ok)})
        else:
            result["note"] = "desired == actual; no action"

        if result["actions"] and not plan_only:
            self._last_action_ts[logical_id] = int(time.time())
            self.store.log_action("autoscale_reconcile", {"logical_id": logical_id, "result": result})

        actual_after = self._count_actual(prefix)
        result["actual_after"] = actual_after
        return result

    def run_once(self, logical_id: str, spec: Dict[str, Any], autoscale_cfg: Dict[str, Any], observed_metrics: Dict[str, float], plan_only: bool = False) -> Dict[str, Any]:
        spec["_autoscale_cfg"] = autoscale_cfg
        desired = self.decide_desired(observed_metrics, autoscale_cfg)
        return self.reconcile(logical_id, spec, desired, plan_only=plan_only)

    def run_loop(self, logical_id: str, spec: Dict[str, Any], autoscale_cfg: Dict[str, Any], metrics_source_callable, poll_interval: int = 30, plan_only: bool = False):
        print(f"[autoscaler] starting loop for {logical_id} (provider={self.provider}) poll_interval={poll_interval}s")
        try:
            while not self._stop_event.is_set():
                observed = metrics_source_callable()
                try:
                    res = self.run_once(logical_id, spec, autoscale_cfg, observed, plan_only=plan_only)
                    print(f"[autoscaler] run: desired={res.get('desired')} actual_before={res.get('actual')} actual_after={res.get('actual_after')} actions={len(res.get('actions',[]))}")
                except Exception as e:
                    print(f"[autoscaler] error during run: {e}")
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            print("[autoscaler] interrupted by user")
        finally:
            print("[autoscaler] stopped")

    def stop(self):
        self._stop_event.set()
