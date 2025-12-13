# LCF/cloud_adapters/noop_adapter.py
"""
Noop compute adapter for CloudBrew.

Provides a class named `NoopComputeAdapter` (important — registry imports this name).
This adapter persists fake instances into the shared SQLiteStore so autoscaler
and CLI can observe/create/destroy fake resources.
"""

from __future__ import annotations
import time
import uuid
from typing import Any, Dict, List, Optional

from LCF import store

ADAPTER_NAME = "noop"

class NoopComputeAdapter:
    def __init__(self, db_path: Optional[str] = None):
        # use the same SQLiteStore used elsewhere (in-memory by default)
        self.store = store.SQLiteStore(db_path)

    # Modern protocol-friendly signature (used by AutoscalerManager)
    def create_instance(self, logical_id: str, spec: Dict[str, Any], plan_only: bool = False) -> Dict[str, Any]:
        """
        Create a fake instance. If plan_only is True, return a Plan-like dict.
        Otherwise persist an instance row and return an Apply-like dict.
        """
        plan_id = f"plan-{uuid.uuid4().hex[:8]}"
        diff = f"[noop] would create {logical_id}"
        if plan_only:
            return {"plan_id": plan_id, "diff": diff}
        adapter_id = f"noop-{uuid.uuid4().hex[:8]}"
        inst = {
            "logical_id": logical_id,
            "adapter": ADAPTER_NAME,
            "adapter_id": adapter_id,
            "spec": spec,
            "state": "running",
            "created_at": int(time.time()),
        }
        self.store.upsert_instance(inst)
        return {"success": True, "adapter_id": adapter_id, "output": diff}

    # Backwards-compatible minimal API (your earlier version used this)
    def create_instance_legacy(self, name: str, image: str, size: str, region: str) -> Dict[str, Any]:
        """Legacy helper if some code calls create_instance(name, image, size, region)."""
        logical_id = name
        spec = {"image": image, "size": size, "region": region}
        return self.create_instance(logical_id, spec, plan_only=False)

    # Also accept very old callers that call create_instance with legacy signature
    def __getattr__(self, name):
        # make adapter forgiving: if someone calls create_instance(name=...,image=...), route it
        if name == "create_instance" and False:
            # unreachable placeholder; kept for clarity
            pass
        raise AttributeError(name)

    # Destroy using adapter_id (modern) and legacy delete_instance (old name)
    def destroy_instance(self, adapter_id: str) -> bool:
        """Destroy a fake instance by adapter_id."""
        return self.store.delete_instance_by_adapter_id(adapter_id)

    # Keep legacy name too (delete_instance)
    def delete_instance(self, instance_id: str) -> bool:
        return self.destroy_instance(instance_id)

    def list_instances(self, filter: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Return list of instances created by noop adapter."""
        # delegate to the store
        return self.store.list_instances(adapter=ADAPTER_NAME)

    # plan/apply stubs for parity with IaC adapters
    def plan(self, logical_id: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        return {"plan_id": f"plan-{uuid.uuid4().hex[:8]}", "diff": f"[noop] plan for {logical_id}"}

    def apply_plan(self, plan_id: str) -> Dict[str, Any]:
        return {"success": True, "adapter_id": None, "output": f"[noop] applied {plan_id}"}
