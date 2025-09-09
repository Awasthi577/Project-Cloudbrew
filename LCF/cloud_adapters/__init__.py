# LCF/cloud_adapters/__init__.py
"""
Lazy adapter registry — importing adapters only when requested to avoid
circular imports during test collection / package import time.
"""

from typing import Dict, Type, Optional
from .protocol import ComputeAdapter

_REGISTRY: Dict[str, Type[ComputeAdapter]] = {}

def register_adapter(provider: str, adapter_cls: Type[ComputeAdapter]) -> None:
    _REGISTRY[provider.lower()] = adapter_cls

def get_compute_adapter(name: str = "noop", **kwargs) -> ComputeAdapter:
    key = (name or "noop").lower()
    Adapter = _REGISTRY.get(key)
    if Adapter:
        return Adapter(**kwargs)
    # lazy import fallback: import known adapters on first request
    if key == "noop":
        from .noop_adapter import NoopComputeAdapter
        register_adapter("noop", NoopComputeAdapter)
        return NoopComputeAdapter(**kwargs)
    if key == "terraform":
        from .terraform_adapter import TerraformAdapter   # may raise if file missing
        register_adapter("terraform", TerraformAdapter)
        return TerraformAdapter(**kwargs)
    if key == "pulumi":
        from .pulumi_adapter import PulumiAdapter
        register_adapter("pulumi", PulumiAdapter)
        return PulumiAdapter(**kwargs)
    raise ValueError(f"No compute adapter registered for provider '{name}'")
