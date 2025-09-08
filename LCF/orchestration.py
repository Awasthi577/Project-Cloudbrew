import os
import time
from typing import Any, Dict, Optional

import boto3

from .utils import save_state


def _get_boto_client(service: str) -> Any:

    endpoint: Optional[str] = os.getenv("AWS_ENDPOINT_URL")
    region: Optional[str] = os.getenv("AWS_REGION", "us-east-1")
    aws_key: Optional[str] = os.getenv("AWS_ACCESS_KEY_ID", "test")
    aws_secret: Optional[str] = os.getenv("AWS_SECRET_ACCESS_KEY", "test")

    # Call boto3.client with explicit keyword args so mypy can match an overload.
    # Passing None for optional args is accepted by boto3 and keeps runtime behavior.
    return boto3.client(
        service_name=service,
        endpoint_url=endpoint if endpoint is not None else None,
        region_name=region if region is not None else None,
        aws_access_key_id=aws_key if aws_key is not None else None,
        aws_secret_access_key=aws_secret if aws_secret is not None else None,
    )


def create_vm(
    name: str, image: str = "ubuntu", size: str = "micro", region: str = "us-east-1"
):
    """
    Minimal create VM flow — writes to local state and (optionally) calls EC2 run_instances
    if AWS_ENDPOINT_URL is set (LocalStack).
    """
    print(
        f"[orchestration] create VM -> name={name}, image={image}, size={size}, region={region}"
    )
    created = {
        "type": "vm",
        "name": name,
        "image": image,
        "size": size,
        "region": region,
        "status": "created",
        "ts": int(time.time()),
    }
    # Save to local state
    save_state({f"vm:{name}": created})

    # If LocalStack endpoint provided, attempt to create EC2 instance (best-effort)
    endpoint = os.getenv("AWS_ENDPOINT_URL")
    if endpoint:
        try:
            ec2 = _get_boto_client("ec2")
            # call a light-weight dry-run: describe or run with minimal args
            # For LocalStack we attempt run_instances with a small placeholder
            resp = ec2.run_instances(
                ImageId="ami-12345",
                InstanceType="t2.micro",
                MinCount=1,
                MaxCount=1,
            )
            print("[orchestration] EC2 run_instances response keys:", list(resp.keys()))
            created["cloud_instance"] = resp.get("Instances", [{}])[0].get(
                "InstanceId", "local-1"
            )
            save_state({f"vm:{name}": created})
        except Exception as e:
            print("[orchestration] Warning: cloud create failed (non-fatal) —", e)
    return created


def create_from_spec(spec: Dict):
    """
    Spec format (simple):
    resources:
      - type: vm
        name: myvm
        image: ubuntu
        size: micro
        region: us-east-1
    """
    results = []
    for r in spec.get("resources", []):
        t = r.get("type")
        if t == "vm":
            name = r.get("name") or f"vm-{int(time.time())}"
            results.append(
                create_vm(
                    name=name,
                    image=r.get("image", "ubuntu"),
                    size=r.get("size", "micro"),
                    region=r.get("region", "us-east-1"),
                )
            )
        else:
            print(f"[orchestration] unsupported resource type {t}, skipping.")
    return results


class Orchestrator:
    """
    Minimal Orchestrator placeholder so tests pass.
    Extend later with orchestration lifecycle methods.
    """

    def __init__(self):
        pass
