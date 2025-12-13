# LCF/cloud_adapters/__init__.py
import os
import logging
from typing import Any, Dict, Optional, Protocol, Type

# Configure logging
logger = logging.getLogger("cloudbrew.adapters")

class ComputeAdapter(Protocol):
    """
    Protocol defining the interface for all compute adapters (OpenTofu, Pulumi, Noop).
    """
    def create_instance(self, logical_id: str, spec: Dict[str, Any], plan_only: bool = False) -> Dict[str, Any]: ...
    def destroy_instance(self, adapter_id: str) -> bool: ...
    def plan(self, logical_id: str, spec: Dict[str, Any]) -> Dict[str, Any]: ...
    def apply_plan(self, plan_id: str, **kwargs) -> Dict[str, Any]: ...

_REGISTRY: Dict[str, Type[ComputeAdapter]] = {}

def register_adapter(provider: str, adapter_cls: Type[ComputeAdapter]) -> None:
    """Register a new adapter class for a specific provider string."""
    _REGISTRY[provider.lower()] = adapter_cls

def get_compute_adapter(provider: str = "noop", **kwargs) -> ComputeAdapter:
    
    key = (provider or "noop").lower()

    # --- STRICT BLOCKING LOGIC ---
    if key == "terraform":
        raise ValueError(
        )
    # -----------------------------

    Adapter = _REGISTRY.get(key)
    if Adapter is None:
        # Lazy loading logic to avoid heavy imports until needed
        try:
            if key in ("opentofu", "tofu"):
                from .opentofu_adapter import OpenTofuAdapter as Adapter
            elif key == "pulumi":
                from .pulumi_adapter import PulumiAdapter as Adapter
            elif key == "noop":
                from .noop_adapter import NoopComputeAdapter as Adapter
            else:
                logger.warning(f"Unknown provider '{key}'. Falling back to NoopAdapter.")
                from .noop_adapter import NoopComputeAdapter as Adapter
        except ImportError as e:
            logger.error(f"Failed to import adapter for '{key}': {e}")
            from .noop_adapter import NoopComputeAdapter as Adapter
        except Exception as e:
            logger.exception(f"Unexpected error loading adapter for '{key}': {e}")
            from .noop_adapter import NoopComputeAdapter as Adapter
        
        # Cache the class for future lookups
        if Adapter:
            register_adapter(key, Adapter)

    # Instantiate the adapter
    inst = Adapter(**kwargs)

    # Optional: Backhaul Wrapper for telemetry/logging if enabled
    if os.environ.get("CLOUDBREW_BACKHAUL", "0") == "1":
        try:
            from LCF.Backhaul.collector import Collector
            from LCF.Backhaul.wrapper import BackhaulAdapterWrapper
            db_path = os.environ.get("CLOUDBREW_BACKHAUL_DB", "backhaul.db")
            coll = Collector(db_path)
            inst = BackhaulAdapterWrapper(inst, coll)
        except Exception as e:
            logger.warning(f"Failed to initialize Backhaul wrapper: {e}")

    return inst

# Eagerly register noop adapter if available to ensure baseline functionality
try:
    from .noop_adapter import NoopComputeAdapter
    register_adapter("noop", NoopComputeAdapter)
except ImportError:
    pass