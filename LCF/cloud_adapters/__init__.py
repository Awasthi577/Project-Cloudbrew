# LCF/cloud_adapters/__init__.py  (patch snippet)
import os
from typing import Any, Dict, Optional, Protocol, Type

# existing protocol... (keep yours)
class ComputeAdapter(Protocol):
    def create_instance(self, name: str, image: str, size: str, region: str, plan_only: bool = True) -> Optional[Any]: ...
    def delete_instance(self, instance_id: str) -> bool: ...
    def plan(self, logical_id: str, spec: Dict[str, Any]) -> Dict[str, Any]: ...
    def apply_plan(self, plan_id: str, **kwargs) -> Dict[str, Any]: ...

_REGISTRY: Dict[str, Type[ComputeAdapter]] = {}

def register_adapter(provider: str, adapter_cls: Type[ComputeAdapter]) -> None:
    _REGISTRY[provider.lower()] = adapter_cls

def get_compute_adapter(provider: str = "noop", **kwargs) -> ComputeAdapter:
    key = (provider or "noop").lower()
    Adapter = _REGISTRY.get(key)
    if Adapter is None:
        # try lazy imports for common adapters
        try:
            if key == "terraform":
                from .terraform_adapter import TerraformAdapter as Adapter
            elif key == "pulumi":
                from .pulumi_adapter import PulumiAdapter as Adapter
            elif key == "noop":
                from .noop_adapter import NoopComputeAdapter as Adapter
            else:
                # unknown provider -> fallback noop
                from .noop_adapter import NoopComputeAdapter as Adapter
        except Exception:
            from .noop_adapter import NoopComputeAdapter as Adapter
        # register for future
        register_adapter(key, Adapter)

    # instantiate
    inst = Adapter(**kwargs)

    # optionally wrap with backhaul if env var set
    if os.environ.get("CLOUDBREW_BACKHAUL", "0") == "1":
        try:
            from LCF.backhaul.collector import Collector
            from LCF.backhaul.wrapper import BackhaulAdapterWrapper
            coll = Collector(os.environ.get("CLOUDBREW_BACKHAUL_DB", "backhaul.db"))
            inst = BackhaulAdapterWrapper(inst, coll)
        except Exception:
            # if wrapper fails, return raw instance (do not crash)
            pass

    return inst

# Eagerly register noop adapter if available
try:
    from .noop_adapter import NoopComputeAdapter
    register_adapter("noop", NoopComputeAdapter)
except Exception:
    pass
