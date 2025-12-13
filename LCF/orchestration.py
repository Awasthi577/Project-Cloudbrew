import time
from typing import Dict, List, Optional

from .cloud_adapters import get_compute_adapter
from .utils import save_state


def create_vm(
    name: str,
    image: str = "ubuntu",
    size: str = "micro",
    region: str = "us-east-1",
    provider: str = "noop",  # default vendor-neutral provider
) -> Dict:
    """
    Vendor-neutral create VM flow.
    - Saves to local state.
    - Delegates cloud-specific logic to provider adapter.
    """
    print(
        f"[orchestration] create VM -> provider={provider}, "
        f"name={name}, image={image}, size={size}, region={region}"
    )

    created = {
        "type": "vm",
        "name": name,
        "image": image,
        "size": size,
        "region": region,
        "provider": provider,
        "status": "created",
        "ts": int(time.time()),
    }

    # Save local state under provider-prefixed key AND bare key for compatibility
    provider_key = f"{provider}:vm:{name}"
    bare_key = f"vm:{name}"
    save_state({provider_key: created})
    save_state({bare_key: created})

    # Delegate to provider adapter (best-effort)
    try:
        adapter = get_compute_adapter(provider)
        cloud_instance = adapter.create_instance(
            name=name,
            image=image,
            size=size,
            region=region,
        )
        if cloud_instance:
            created["cloud_instance"] = cloud_instance
            # update both keys again with cloud info
            save_state({provider_key: created})
            save_state({bare_key: created})
    except Exception as e:
        print(
            f"[orchestration] Warning: provider {provider} create failed (non-fatal) — {e}"
        )

    return created


def create_from_spec(spec: Dict) -> List[Dict]:
    """
    Parse a vendor-neutral spec and create resources accordingly.

    Example spec:
    resources:
      - type: vm
        name: myvm
        image: ubuntu
        size: micro
        region: us-east-1
        provider: aws|azure|gcp|noop
    """
    results: List[Dict] = []
    for r in spec.get("resources", []):
        rtype = r.get("type")
        if rtype == "vm":
            name = r.get("name") or f"vm-{int(time.time())}"
            results.append(
                create_vm(
                    name=name,
                    image=r.get("image", "ubuntu"),
                    size=r.get("size", "micro"),
                    region=r.get("region", "us-east-1"),
                    provider=r.get("provider", "noop"),  # default vendor-neutral
                )
            )
        else:
            print(f"[orchestration] unsupported resource type {rtype}, skipping.")
    return results


class Orchestrator:
    """
    Vendor-neutral Orchestrator.
    Manages lifecycle of resources via provider adapters.
    """

    def __init__(self, provider: Optional[str] = "noop"):  # default vendor-neutral
        self.provider = provider

    def create_from_spec(self, spec: Dict) -> List[Dict]:
        return create_from_spec(spec)
