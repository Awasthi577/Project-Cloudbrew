"""
CloudBrew LCF package entrypoint.
Keep this lightweight to avoid circular-import issues.
"""

import importlib
from typing import Any

__all__ = [
    "dsl_parser",
    "utils",
    "orchestration",
    "api_handler",
    "create_from_spec",
    "create_vm",
]

# --- Lazy attribute loader ---
def __getattr__(name: str) -> Any:
    if name in ("dsl_parser", "utils", "orchestration", "api_handler"):
        return importlib.import_module(f"LCF.{name}")
    if name in ("create_from_spec", "create_vm"):
        orch = importlib.import_module("LCF.orchestration")
        return getattr(orch, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
