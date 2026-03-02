"""Schema-based provisioning compatibility layer.

Historically CloudBrew exposed a schema provisioner module. This module restores
that import path while delegating provisioning to OpenTofuAdapter.
"""

from __future__ import annotations

from typing import Dict, Any

from LCF.cloud_adapters.opentofu_adapter import OpenTofuAdapter


class SchemaProvisioner:
    def __init__(self, db_path: str | None = None):
        self._adapter = OpenTofuAdapter(db_path=db_path)

    def provision(self, logical_id: str, spec: Dict[str, Any], plan_only: bool = False) -> Dict[str, Any]:
        return self._adapter.create_instance(logical_id, spec, plan_only=plan_only)
