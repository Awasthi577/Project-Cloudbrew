"""
Azure adapter stub — placeholder for community implementation.
"""

from typing import Any, Optional


class AzureComputeAdapter:
    def create_instance(
        self, name: str, image: str, size: str, region: str
    ) -> Optional[Any]:
        print(
            f"[azure adapter] create_instance(name={name}, image={image}, size={size}, region={region})"
        )
        return {"InstanceId": f"azure-{name}"}

    def delete_instance(self, instance_id: str) -> bool:
        print(f"[azure adapter] delete_instance(instance_id={instance_id})")
        return True
