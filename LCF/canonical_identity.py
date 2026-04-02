from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict


_PROVIDER_ALIASES = {
    "azure": "azurerm",
    "az": "azurerm",
    "amazon": "aws",
    "gcp": "google",
    "google_cloud": "google",
    "tofu": "opentofu",
}


def normalize_provider(provider: str | None) -> str:
    p = (provider or "auto").strip().lower()
    return _PROVIDER_ALIASES.get(p, p)


@dataclass(frozen=True)
class CanonicalIdentity:
    provider: str
    resource_type: str
    logical_name: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
