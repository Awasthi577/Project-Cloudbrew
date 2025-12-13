"""
GCP adapter stub — placeholder for community implementation.
"""

from typing import Any, Optional


class GCPComputeAdapter:
    def create_instance(
        self, name: str, image: str, size: str, region: str
    ) -> Optional[Any]:
        print(
            f"[gcp adapter] create_instance(name={name}, image={image}, size={size}, region={region})"
        )
        return {"InstanceId": f"gcp-{name}"}

    def delete_instance(self, instance_id: str) -> bool:
        print(f"[gcp adapter] delete_instance(instance_id={instance_id})")
        return True
