# LCF/cloud_adapters/terraform_adapter.py
"""
Terraform adapter with streaming helpers.

Provides:
- stream_create_instance(logical_id, spec, plan_only=False) -> yields stdout lines
- stream_apply_plan(plan_path) -> yields stdout lines
- create_instance(...) (compat) -> returns dict summary (calls streaming functions internally)
- apply_plan(...) (compat) -> returns dict summary
- plan(...) (compat) -> returns dict summary for plan_only
- destroy_instance(...) (compat) -> delete instance entry from store
"""
from __future__ import annotations
import os
import json
import subprocess
import shutil
import tempfile
import time
from typing import Dict, Any, Optional, Generator

from LCF import store

# TF workdir root (override via env var if needed)
TF_ROOT = os.environ.get("CLOUDBREW_TF_ROOT", r"C:\tmp\.cloudbrew_tf" if os.name == "nt" else ".cloudbrew_tf")
os.makedirs(TF_ROOT, exist_ok=True)


def _run(cmd, cwd=None, env=None, timeout=300):
    """
    Run a command and capture combined stdout/stderr. Returns (rc, output).
    This is used by non-streaming legacy calls.
    """
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except FileNotFoundError as e:
        return 127, f"executable not found: {cmd[0]!r}. ({e})"
    out_lines = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line.rstrip())
            out_lines.append(line)
        proc.wait(timeout=timeout)
        return proc.returncode, "".join(out_lines)
    except subprocess.TimeoutExpired as e:
        try:
            proc.kill()
        except Exception:
            pass
        return 124, f"timeout expired: {e}"
    except Exception as e:
        try:
            proc.kill()
        except Exception:
            pass
        return 1, f"error running command: {e}"


def _stream_subprocess(cmd, cwd=None, env=None, timeout=300) -> Generator[str, None, None]:
    """
    Run subprocess and yield combined stdout/stderr lines as they arrive.
    On non-zero exit, raises RuntimeError with combined output appended.
    """
    try:
        proc = subprocess.Popen(list(cmd), cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    except FileNotFoundError as e:
        # yield a helpful message then raise so consumers can record it
        msg = f"executable not found: {cmd[0]!r}. ({e})"
        yield msg
        raise RuntimeError(msg)

    assert proc.stdout is not None
    out_lines = []
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            out_lines.append(line)
            yield line
        proc.wait(timeout=timeout)
        if proc.returncode != 0:
            full = "\n".join(out_lines)
            raise RuntimeError(f"Command {' '.join(cmd)} failed with code {proc.returncode}:\n{full}")
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass


# small mapping tables (extend as needed)
AWS_IMAGE_MAP = {
    "ubuntu-22.04": "ami-0a63f6f9f6f9abcde",  # replace with real mapping for your region
}
AWS_SIZE_MAP = {
    "small": "t3.micro",
    "medium": "t3.medium",
    "large": "t3.large",
}

# -----------------------
# Provider credential heuristics
# -----------------------
def has_aws_creds() -> bool:
    """Heuristic: check common AWS env vars and credentials file."""
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return True
    if os.environ.get("AWS_SESSION_TOKEN"):
        return True
    if os.environ.get("AWS_PROFILE"):
        return True
    if os.environ.get("AWS_SHARED_CREDENTIALS_FILE"):
        creds_path = os.environ.get("AWS_SHARED_CREDENTIALS_FILE")
        if creds_path and os.path.exists(os.path.expanduser(creds_path)):
            return True
    if os.path.exists(os.path.expanduser("~/.aws/credentials")):
        return True
    return False


def has_azure_creds() -> bool:
    """
    Heuristic: check commonly used Azure environment variables or auth locations.
    - AZURE_CLIENT_ID + AZURE_CLIENT_SECRET + AZURE_TENANT_ID + AZURE_SUBSCRIPTION_ID
    - ARM_CLIENT_ID / ARM_CLIENT_SECRET / ARM_TENANT_ID (legacy)
    - AZURE_AUTH_LOCATION (path to an auth file)
    - presence of ~/.azure as a loose hint
    """
    # service principal envs
    if os.environ.get("AZURE_CLIENT_ID") and os.environ.get("AZURE_CLIENT_SECRET") and os.environ.get("AZURE_TENANT_ID"):
        return True
    if os.environ.get("ARM_CLIENT_ID") and os.environ.get("ARM_CLIENT_SECRET") and os.environ.get("ARM_TENANT_ID"):
        return True
    if os.environ.get("AZURE_AUTH_LOCATION"):
        path = os.path.expanduser(os.environ.get("AZURE_AUTH_LOCATION"))
        if os.path.exists(path):
            return True
    # basic config folder heuristic
    if os.path.exists(os.path.expanduser("~/.azure")):
        return True
    # not guaranteed, but helpful
    return False


def has_gcp_creds() -> bool:
    """
    Heuristic: check for GOOGLE_APPLICATION_CREDENTIALS env or gcloud ADC file.
    - GOOGLE_APPLICATION_CREDENTIALS points to a service account JSON
    - ~/.config/gcloud/application_default_credentials.json exists
    """
    gac = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if gac and os.path.exists(os.path.expanduser(gac)):
        return True
    # Common ADC path on Linux/Mac/Windows (expanduser handles Windows)
    adc = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
    if os.path.exists(adc):
        return True
    # On Windows, gcloud may store ADC under %APPDATA%/gcloud -> but we keep the simple heuristic
    return False


def has_provider_creds(provider: str) -> bool:
    """
    Generic provider credential check. provider is lowercased, e.g. 'aws','azure','gcp'
    """
    p = (provider or "aws").lower()
    if p == "aws":
        return has_aws_creds()
    if p in ("azure", "azurerm"):
        return has_azure_creds()
    if p in ("gcp", "google", "google_cloud"):
        return has_gcp_creds()
    # unknown provider -> assume no creds
    return False


def translate_vm_spec_to_hcl(logical_id: str, spec: Dict[str, Any]) -> str:
    """
    Translate canonical VM spec to provider HCL when credentials are present (or forced).
    Otherwise return a null_resource placeholder HCL with an explanatory comment.
    Supports: aws, azure, gcp; falls back to null_resource for unknown providers.
    """
    provider = (spec.get("provider") or "aws").lower()
    name = logical_id.replace("-", "_").replace(".", "_")

    # allow forcing provider behaviour via env, e.g. CLOUDBREW_TF_FORCE_AWS=1
    force_flag = os.environ.get(f"CLOUDBREW_TF_FORCE_{provider.upper()}", "0") == "1"

    creds_ok = has_provider_creds(provider) or force_flag

    if provider == "aws" and creds_ok:
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

    if provider in ("azure", "azurerm") and creds_ok:
        # Minimal azurerm sample - replace with more complete mapping as needed
        # Note: real azure resources typically require resource_group, network, etc.
        location = spec.get("region", "eastus")
        # Use simple placeholder VM (user should provide necessary networking in real spec)
        hcl = f'''
provider "azurerm" {{
  features = {{}}
}}

resource "azurerm_resource_group" "{name}_rg" {{
  name     = "{logical_id}-rg"
  location = "{location}"
}}

# Placeholder VM - in real usage you should translate image/size to proper Azure args
resource "null_resource" "{name}_placeholder" {{
  provisioner "local-exec" {{
    command = "echo 'azure vm placeholder for {logical_id} in {location}'"
  }}
}}
'''
        return hcl

    if provider in ("gcp", "google") and creds_ok:
        zone = spec.get("region", "us-central1-a")
        # Minimal GCP placeholder using google_compute_instance would require network configs;
        # use a null_resource note or a minimal compute instance example if you prefer.
        hcl = f'''
provider "google" {{
  project = var.project_id
  region  = "{zone}"
}}

# Fallback placeholder for GCP - replace with full translation for actual provisioning
resource "null_resource" "{name}_placeholder" {{
  provisioner "local-exec" {{
    command = "echo 'gcp vm placeholder for {logical_id} in {zone}'"
  }}
}}
'''
        return hcl

    # Unknown provider or creds missing: generate null_resource fallback and add comment explaining why
    reason = f"# fallback: provider={provider}. credentials detected? {creds_ok}. To force real provider set CLOUDBREW_TF_FORCE_{provider.upper()}=1\n"
    hcl = f'''
{reason}
resource "null_resource" "{name}" {{
  provisioner "local-exec" {{
    command = "echo 'would create {logical_id} (provider={provider}, image={spec.get('image')}, size={spec.get('size')})'"
  }}
}}
'''
    return hcl


class TerraformAdapter:
    def __init__(self, db_path: Optional[str] = None):
        self.store = store.SQLiteStore(db_path)
        self.terraform_path = os.environ.get("CLOUDBREW_TERRAFORM_BIN") or shutil.which("terraform")
        if not self.terraform_path:
            # warning printed but adapter remains usable in fallback mode
            print("[terraform_adapter] warning: terraform not found on PATH. Using fallback behavior.")

    def _workdir_for(self, logical_id: str) -> str:
        safe = logical_id.replace(":", "-").replace("/", "-")
        d = os.path.join(TF_ROOT, safe)
        os.makedirs(d, exist_ok=True)
        return d

    # -----------------------
    # Streaming API
    # -----------------------
    def stream_create_instance(
        self, logical_id: str, spec: Dict[str, Any], plan_only: bool = False
    ) -> Generator[str, None, None]:
        """
        Create a temp/working Terraform project for logical_id, write main.tf and spec.json,
        run `terraform init` then `terraform plan` and (optionally) `terraform apply`.
        Yields each output line.
        On success, yields a summary line containing 'PLAN_SAVED:<plan_path>' or 'APPLY_COMPLETE'.
        """
        wd = self._workdir_for(logical_id)
        main_tf = translate_vm_spec_to_hcl(logical_id, spec)
        with open(os.path.join(wd, "main.tf"), "w", encoding="utf-8") as fh:
            fh.write(main_tf)
        # save spec for visibility
        with open(os.path.join(wd, "spec.json"), "w", encoding="utf-8") as fh:
            json.dump(spec, fh, indent=2)

        # fallback if terraform missing
        if not self.terraform_path:
            msg = f"[terraform-adapter fallback] would create resources for {logical_id}"
            yield msg
            yield f"HCL:\n{main_tf}"
            if plan_only:
                yield f"PLAN_SAVED:{os.path.abspath(os.path.join(wd, 'plan.tfplan'))}"
                return
            yield "APPLY_COMPLETE"
            return

        env = os.environ.copy()
        env["TF_IN_AUTOMATION"] = "1"

        # init
        yield from _stream_subprocess(
            [self.terraform_path, "init", "-input=false", "-no-color"], cwd=wd, env=env
        )

        # plan -> write plan file
        plan_file = os.path.abspath(os.path.join(wd, "plan.tfplan"))
        yield from _stream_subprocess(
            [self.terraform_path, "plan", "-out", plan_file, "-input=false", "-no-color"],
            cwd=wd,
            env=env,
        )

        yield f"PLAN_SAVED:{plan_file}"

        if plan_only:
            return

        # apply saved plan
        yield from _stream_subprocess(
            [self.terraform_path, "apply", "-auto-approve", plan_file, "-input=false", "-no-color"],
            cwd=wd,
            env=env,
        )
        yield "APPLY_COMPLETE"

    def stream_apply_plan(self, plan_path: str) -> Generator[str, None, None]:
        """
        Stream applying an existing plan file. Assumes plan_path points to a plan in a workdir.
        """
        if not os.path.exists(plan_path):
            yield f"PLAN_NOT_FOUND:{plan_path}"
            raise RuntimeError(f"plan file not found: {plan_path}")

        wd = os.path.dirname(plan_path)

        if not self.terraform_path:
            yield f"[terraform-adapter fallback] would apply plan {plan_path}"
            yield "APPLY_COMPLETE"
            return

        env = os.environ.copy()
        env["TF_IN_AUTOMATION"] = "1"
        yield from _stream_subprocess(
            [self.terraform_path, "apply", "-auto-approve", plan_path, "-input=false", "-no-color"],
            cwd=wd,
            env=env,
        )
        yield "APPLY_COMPLETE"

    def stream_destroy_instance(self, logical_id: str) -> Generator[str, None, None]:
        """
        Stream a destroy run for the given logical_id's workdir.
        Yields terraform output lines, ending with DESTROY_COMPLETE.
        """
        wd = self._workdir_for(logical_id)

        if not self.terraform_path:
            yield f"[terraform-adapter fallback] would destroy {logical_id}"
            yield "DESTROY_COMPLETE"
            return

        env = os.environ.copy()
        env["TF_IN_AUTOMATION"] = "1"
        yield from _stream_subprocess(
            [self.terraform_path, "destroy", "-auto-approve", "-no-color"],
            cwd=wd,
            env=env,
        )
        yield "DESTROY_COMPLETE"

    # -----------------------
    # Backwards-compatible convenience methods (non-streaming)
    # -----------------------
    def create_instance(self, logical_id: str, spec: Dict[str, Any], plan_only: bool = False) -> Dict[str, Any]:
        try:
            gen = self.stream_create_instance(logical_id, spec, plan_only=plan_only)
            last = None
            plan_path = None
            for ln in gen:
                last = ln
                if isinstance(ln, str) and ln.startswith("PLAN_SAVED:"):
                    plan_path = ln.split("PLAN_SAVED:", 1)[1]
            if plan_only:
                return {"plan_id": plan_path, "diff": last}
            return {"success": True, "adapter_id": f"terraform-{logical_id}", "output": last}
        except RuntimeError as e:
            return {"success": False, "error": str(e)}

    def apply_plan(self, plan_id: str) -> Dict[str, Any]:
        try:
            gen = self.stream_apply_plan(plan_id)
            last = None
            for ln in gen:
                last = ln
            return {"success": True, "output": last}
        except RuntimeError as e:
            return {"success": False, "error": str(e)}

    def plan(self, logical_id: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        return self.create_instance(logical_id, spec, plan_only=True)

    def destroy_instance(self, adapter_id: str) -> bool:
        logical_id = adapter_id.replace("terraform-", "")
        try:
            for _ in self.stream_destroy_instance(logical_id):
                pass
            return self.store.delete_instance_by_adapter_id(adapter_id)
        except Exception:
            return False
