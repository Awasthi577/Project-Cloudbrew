"""
AWS adapter stub — optional, uses boto3 if available.
"""

import os
from typing import Any, Optional


class AWSComputeAdapter:
    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import boto3
            except ImportError:
                print("[aws adapter] boto3 not installed, running in stub mode")
                return None

            endpoint = os.getenv("AWS_ENDPOINT_URL")
            region = os.getenv("AWS_REGION", "us-east-1")
            aws_key = os.getenv("AWS_ACCESS_KEY_ID")
            aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY")

            self._client = boto3.client(
                "ec2",
                endpoint_url=endpoint or None,
                region_name=region or None,
                aws_access_key_id=aws_key or None,
                aws_secret_access_key=aws_secret or None,
            )
        return self._client

    def create_instance(
        self, name: str, image: str, size: str, region: str
    ) -> Optional[Any]:
        client = self._get_client()
        if client is None:
            return {"InstanceId": f"aws-stub-{name}"}
        resp = client.run_instances(
            ImageId="ami-12345", InstanceType="t2.micro", MinCount=1, MaxCount=1
        )
        return resp.get("Instances", [{}])[0].get("InstanceId")

    def delete_instance(self, instance_id: str) -> bool:
        client = self._get_client()
        if client is None:
            return True
        client.terminate_instances(InstanceIds=[instance_id])
        return True
