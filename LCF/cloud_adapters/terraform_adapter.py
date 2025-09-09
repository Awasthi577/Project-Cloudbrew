# LCF/cloud_adapters/terraform_adapter.py
"""
Terraform adapter with a small translator for canonical VM specs.
Translates canonical spec -> HCL (AWS EC2), falls back to null_resource otherwise.
Safe defaults: plan-only fallback if terraform missing.
"""

from __future__ import annotations
import os
import json
import subprocess
import shutil
import time
from typing import Dict, Any, Optional

from LCF import store

# TF workdir root (override via env var if needed)
TF_ROOT = os.environ.get("CLOUDBREW_TF_ROOT", r"C:\tmp\.cloudbrew_tf" if os.name == "nt" else ".cloudbrew_tf")
os.makedirs(TF_ROOT, exist_ok=True)


def _run(cmd, cwd=None, env=None, timeout=300):
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
        return proc.returncode, proc.stdout
    except FileNotFoundError as e:
        return 127, f"executable not found: {cmd[0]!r}. Install or set CLOUDBREW_TERRAFORM_BIN. ({e})"
    except subprocess.TimeoutExpired as e:
        return 124, f"timeout expired: {e}"


# small mapping tables (extend as needed)
AWS_IMAGE_MAP = {
    "ubuntu-22.04": "ami-0a63f6f9f6f9abcde",  # <-- replace with real mappings for your region
}
AWS_SIZE_MAP = {
    "small": "t3.micro",
    "medium": "t3.medium",
    "large": "t3.large",
}


def translate_vm_spec_to_hcl(logical_id: str, spec: Dict[str, Any]) -> str:
    """
    Translate canonical VM spec to Terraform HCL. If provider == 'aws' generate aws_instance resource.
    Otherwise return a null_resource as a safe placeholder.
    """
    provider = spec.get("provider", "aws")
    name = logical_id.replace("-", "_").replace(".", "_")
    if provider == "aws":
        # map canonical image/size
        image_key = spec.get("image", "ubuntu-22.04")
        instance_type = AWS_SIZE_MAP.get(spec.get("size", "small"), "t3.micro")
        ami = AWS_IMAGE_MAP.get(image_key, "ami-0a63f6f9f6f9abcde")
        region = spec.get("region", "us-east-1")
        hcl = f'''
provider "aws" {{
  region = "{region}"
}}

resource "aws_instance" "{name}" {{
  ami           = "{ami}"
  instance_type = "{instance_type}"
  tags = {{
    Name = "{logical_id}"
  }}
}}
'''
        return hcl

    # fallback: safe null_resource
    hcl = f'''
# fallback null_resource for {logical_id}
resource "null_resource" "{name}" {{
  provisioner "local-exec" {{
    command = "echo 'would create {logical_id} ({spec.get('image')}/{spec.get('size')})'"
  }}
}}
'''
    return hcl


class TerraformAdapter:
    def __init__(self, db_path: Optional[str] = None):
        self.store = store.SQLiteStore(db_path)
        self.terraform_path = os.environ.get("CLOUDBREW_TERRAFORM_BIN") or shutil.which("terraform")
        if not self.terraform_path:
            print("[terraform_adapter] warning: terraform not found on PATH. Using fallback plan/apply behavior.")

    def _workdir_for(self, logical_id: str) -> str:
        safe = logical_id.replace(":", "-").replace("/", "-")
        d = os.path.join(TF_ROOT, safe)
        os.makedirs(d, exist_ok=True)
        return d

    def create_instance(self, logical_id: str, spec: Dict[str, Any], plan_only: bool = False) -> Dict[str, Any]:
        wd = self._workdir_for(logical_id)
        # Render HCL from translator
        main_tf = translate_vm_spec_to_hcl(logical_id, spec)
        with open(os.path.join(wd, "main.tf"), "w", encoding="utf-8") as fh:
            fh.write(main_tf)

        # If terraform missing, fallback
        if not self.terraform_path:
            plan_text = f"[terraform-adapter fallback plan] would create resources for {logical_id}\n\nHCL:\n{main_tf}"
            if plan_only:
                return {"plan_id": f"plan-fallback-{logical_id}", "diff": plan_text}
            adapter_id = f"tf-fake-{logical_id}"
            inst = {
                "logical_id": logical_id,
                "adapter": "terraform",
                "adapter_id": adapter_id,
                "spec": spec,
                "state": "running",
                "created_at": int(time.time())
            }
            self.store.upsert_instance(inst)
            return {"success": True, "adapter_id": adapter_id, "output": plan_text}

        # Run terraform init / plan / apply
        rc, out = _run([self.terraform_path, "init", "-input=false", "-no-color"], cwd=wd)
        if rc != 0:
            return {"success": False, "output": out, "error": "terraform init failed"}

        plan_file = os.path.abspath(os.path.join(wd, "plan.tfplan"))
        rc, out = _run([self.terraform_path, "plan", "-out", plan_file, "-input=false", "-no-color"], cwd=wd)
        if rc != 0:
            return {"success": False, "output": out, "error": "terraform plan failed", "plan_out": out}

        if plan_only:
            return {"plan_id": plan_file, "diff": out}

        rc, out = _run([self.terraform_path, "apply", "-auto-approve", plan_file, "-no-color"], cwd=wd)
        if rc != 0:
            return {"success": False, "output": out, "error": "terraform apply failed"}

        adapter_id = f"terraform-{logical_id}"
        inst = {
            "logical_id": logical_id,
            "adapter": "terraform",
            "adapter_id": adapter_id,
            "spec": spec,
            "state": "running",
            "created_at": int(time.time())
        }
        self.store.upsert_instance(inst)
        return {"success": True, "adapter_id": adapter_id, "output": out}

    def plan(self, logical_id: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        return self.create_instance(logical_id, spec, plan_only=True)

    def apply_plan(self, plan_id: str) -> Dict[str, Any]:
        wd = os.path.dirname(plan_id)
        if not self.terraform_path:
            return {"success": False, "output": f"terraform not installed; cannot apply {plan_id}"}
        rc, out = _run([self.terraform_path, "apply", "-auto-approve", plan_id, "-no-color"], cwd=wd)
        if rc != 0:
            return {"success": False, "output": out, "error": "terraform apply failed"}
        return {"success": True, "adapter_id": f"terraform-applied-{os.path.basename(wd)}", "output": out}

    def destroy_instance(self, adapter_id: str) -> bool:
        return self.store.delete_instance_by_adapter_id(adapter_id)

    def list_instances(self, filter: Optional[Dict[str, Any]] = None):
        return self.store.list_instances(adapter="terraform")
