# LCF/cloud_adapters/terraform_adapter.py
"""
Terraform adapter with streaming helpers + caching.

Provides:
- stream_create_instance(logical_id, spec, plan_only=False) -> yields stdout lines
- stream_apply_plan(plan_path) -> yields stdout lines
- stream_destroy_instance(logical_id) -> yields stdout lines
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
import time
import sqlite3
from typing import Dict, Any, Optional, Generator, List

from LCF import store


def _build_terraform_apply_cmd(terraform_path: str, plan_path: Optional[str] = None, extra_flags: Optional[List[str]] = None) -> List[str]:
    """
    Build a terraform apply command that places flags BEFORE any positional plan file.
    Returns a list suitable for subprocess.
    Example:
      ['terraform', 'apply', '-auto-approve', '-input=false', '-no-color', 'path/to/plan.tfplan']
    """
    base = [terraform_path or "terraform", "apply"]
    flags = ["-auto-approve", "-input=false", "-no-color"]
    if extra_flags:
        flags = flags + list(extra_flags)
    cmd = base + flags
    if plan_path:
        cmd.append(plan_path)
    return cmd


def _build_terraform_destroy_cmd(terraform_path: str) -> List[str]:
    """
    Build a terraform destroy command with flags before any positional args.
    """
    return [terraform_path or "terraform", "destroy", "-auto-approve", "-input=false", "-no-color"]


# ---------------------------------------------------------------------
# Directories
# ---------------------------------------------------------------------
TF_ROOT = os.environ.get("CLOUDBREW_TF_ROOT", r"C:\tmp\.cloudbrew_tf" if os.name == "nt" else ".cloudbrew_tf")
os.makedirs(TF_ROOT, exist_ok=True)

CACHE_DIR = ".cloudbrew_cache"
CACHE_DB = os.path.join(CACHE_DIR, "resources.db")
os.makedirs(CACHE_DIR, exist_ok=True)


def _ensure_cache_db():
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS resource_mappings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider TEXT,
        logical_type TEXT,
        tf_resource TEXT,
        created_at INTEGER,
        UNIQUE(provider, logical_type)
    );
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _run(cmd, cwd=None, env=None, timeout=300):
    """
    Run a command (list form) and return (rc, combined stdout/stderr).
    Prints stdout lines to the calling environment for visibility.
    """
    try:
        proc = subprocess.Popen(list(cmd), cwd=cwd, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
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
        proc = subprocess.Popen(list(cmd), cwd=cwd, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
    except FileNotFoundError as e:
        msg = f"executable not found: {cmd[0]!r}. ({e})"
        yield msg
        raise RuntimeError(msg)

    assert proc.stdout is not None
    out_lines: List[str] = []
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


# ---------------------------------------------------------------------
# Image/size maps
# ---------------------------------------------------------------------
AWS_IMAGE_MAP = {"ubuntu-22.04": "ami-0a63f6f9f6f9abcde"}  # placeholder
AWS_SIZE_MAP = {"small": "t3.micro", "medium": "t3.medium", "large": "t3.large"}


# ---------------------------------------------------------------------
# Credential heuristics
# ---------------------------------------------------------------------
def has_aws_creds() -> bool:
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return True
    if os.environ.get("AWS_SESSION_TOKEN"):
        return True
    if os.environ.get("AWS_PROFILE"):
        return True
    if os.environ.get("AWS_SHARED_CREDENTIALS_FILE"):
        path = os.environ["AWS_SHARED_CREDENTIALS_FILE"]
        if os.path.exists(os.path.expanduser(path)):
            return True
    if os.path.exists(os.path.expanduser("~/.aws/credentials")):
        return True
    return False


def has_azure_creds() -> bool:
    if os.environ.get("AZURE_CLIENT_ID") and os.environ.get("AZURE_CLIENT_SECRET") and os.environ.get("AZURE_TENANT_ID"):
        return True
    if os.environ.get("ARM_CLIENT_ID") and os.environ.get("ARM_CLIENT_SECRET") and os.environ.get("ARM_TENANT_ID"):
        return True
    if os.environ.get("AZURE_AUTH_LOCATION"):
        path = os.path.expanduser(os.environ.get("AZURE_AUTH_LOCATION"))
        if os.path.exists(path):
            return True
    if os.path.exists(os.path.expanduser("~/.azure")):
        return True
    return False


def has_gcp_creds() -> bool:
    gac = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if gac and os.path.exists(os.path.expanduser(gac)):
        return True
    adc = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
    if os.path.exists(adc):
        return True
    return False


def has_provider_creds(provider: str) -> bool:
    p = (provider or "aws").lower()
    if p == "aws":
        return has_aws_creds()
    if p in ("azure", "azurerm"):
        return has_azure_creds()
    if p in ("gcp", "google", "google_cloud"):
        return has_gcp_creds()
    return False


# ---------------------------------------------------------------------
# TerraformAdapter
# ---------------------------------------------------------------------
class TerraformAdapter:
    def __init__(self, db_path: Optional[str] = None):
        self.store = store.SQLiteStore(db_path)
        # allow overriding binary location explicitly
        self.terraform_path = os.environ.get("CLOUDBREW_TERRAFORM_BIN") or shutil.which("terraform")
        if not self.terraform_path:
            print("[terraform_adapter] warning: terraform not found. Using fallback.")
        # sqlite cache for resource mappings
        self.conn = _ensure_cache_db()

    def _workdir_for(self, logical_id: str) -> str:
        safe = logical_id.replace(":", "-").replace("/", "-")
        d = os.path.join(TF_ROOT, safe)
        os.makedirs(d, exist_ok=True)
        return d

    def _cache_mapping(self, provider: str, logical: str, tf_res: str):
        try:
            cur = self.conn.cursor()
            cur.execute(
                "INSERT OR IGNORE INTO resource_mappings(provider, logical_type, tf_resource, created_at) VALUES(?,?,?,?)",
                (provider, logical, tf_res, int(time.time())),
            )
            self.conn.commit()
            cur.close()
        except Exception:
            pass

    def _cleanup(self, workdir: str):
        for fname in ["main.tf", "plan.tfplan", "spec.json"]:
            f = os.path.join(workdir, fname)
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass
        tfdir = os.path.join(workdir, ".terraform")
        if os.path.isdir(tfdir):
            try:
                shutil.rmtree(tfdir)
            except Exception:
                pass

    # -------------------------
    # Streaming
    # -------------------------
    def stream_create_instance(self, logical_id: str, spec: Dict[str, Any], plan_only: bool = False) -> Generator[str, None, None]:
        wd = self._workdir_for(logical_id)

        # translate spec -> HCL
        provider = (spec.get("provider") or "aws").lower()
        name = logical_id.replace("-", "_")
        creds_ok = has_provider_creds(provider)

        if provider == "aws" and creds_ok:
            ami = AWS_IMAGE_MAP.get(spec.get("image", "ubuntu-22.04"), "ami-0a63f6f9f6f9abcde")
            inst_type = AWS_SIZE_MAP.get(spec.get("size", "small"), "t3.micro")
            region = spec.get("region", "us-east-1")
            tf_res = "aws_instance"
            hcl = f"""
provider "aws" {{
  region = "{region}"
}}

resource "{tf_res}" "{name}" {{
  ami           = "{ami}"
  instance_type = "{inst_type}"
  tags = {{
    Name = "{logical_id}"
  }}
}}
"""
            self._cache_mapping("aws", "vm", tf_res)
        else:
            tf_res = "null_resource"
            hcl = f"""
resource "{tf_res}" "{name}" {{
  provisioner "local-exec" {{
    command = "echo 'would create {logical_id} (provider={provider})'"
  }}
}}
"""
            self._cache_mapping(provider, spec.get("type", "vm"), tf_res)

        with open(os.path.join(wd, "main.tf"), "w", encoding="utf-8") as fh:
            fh.write(hcl)
        with open(os.path.join(wd, "spec.json"), "w", encoding="utf-8") as fh:
            json.dump(spec, fh)

        # fallback when terraform binary isn't available
        if not self.terraform_path:
            yield f"[terraform-adapter fallback] {logical_id}"
            yield f"HCL:\n{hcl}"
            if plan_only:
                yield f"PLAN_SAVED:{os.path.abspath(os.path.join(wd, 'plan.tfplan'))}"
                self._cleanup(wd)
                return
            yield "APPLY_COMPLETE"
            self._cleanup(wd)
            return

        env = os.environ.copy()
        env["TF_IN_AUTOMATION"] = "1"

        # --- Windows shell env fix: ensure cmd.exe & system32 visible to terraform local-exec ---
        if os.name == "nt":
            env.setdefault("SystemRoot", os.environ.get("SystemRoot", r"C:\Windows"))
            env.setdefault("COMSPEC", os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"))
            system32 = os.path.join(env["SystemRoot"], "System32")
            if system32 not in env.get("PATH", ""):
                env["PATH"] = system32 + os.pathsep + env.get("PATH", "")

        # init
        yield from _stream_subprocess([self.terraform_path, "init", "-input=false", "-no-color"], cwd=wd, env=env)

        # plan -> write plan file
        plan_file = os.path.abspath(os.path.join(wd, "plan.tfplan"))
        yield from _stream_subprocess([self.terraform_path, "plan", "-out", plan_file, "-input=false", "-no-color"], cwd=wd, env=env)

        yield f"PLAN_SAVED:{plan_file}"
        if plan_only:
            self._cleanup(wd)
            return

        # apply using the safe builder (flags before plan path)
        apply_cmd = _build_terraform_apply_cmd(self.terraform_path, plan_file)
        yield from _stream_subprocess(apply_cmd, cwd=wd, env=env)

        yield "APPLY_COMPLETE"
        self._cleanup(wd)

    def stream_apply_plan(self, plan_path: str) -> Generator[str, None, None]:
        if not os.path.exists(plan_path):
            yield f"PLAN_NOT_FOUND:{plan_path}"
            raise RuntimeError(f"plan not found: {plan_path}")
        wd = os.path.dirname(plan_path)
        if not self.terraform_path:
            yield f"[terraform-adapter fallback] apply {plan_path}"
            yield "APPLY_COMPLETE"
            return

        env = os.environ.copy()
        env["TF_IN_AUTOMATION"] = "1"

        # Windows env fix for local-exec in applied plan
        if os.name == "nt":
            env.setdefault("SystemRoot", os.environ.get("SystemRoot", r"C:\Windows"))
            env.setdefault("COMSPEC", os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"))
            system32 = os.path.join(env["SystemRoot"], "System32")
            if system32 not in env.get("PATH", ""):
                env["PATH"] = system32 + os.pathsep + env.get("PATH", "")

        apply_cmd = _build_terraform_apply_cmd(self.terraform_path, plan_path)
        yield from _stream_subprocess(apply_cmd, cwd=wd, env=env)
        yield "APPLY_COMPLETE"
        self._cleanup(wd)

    def stream_destroy_instance(self, logical_id: str) -> Generator[str, None, None]:
        wd = self._workdir_for(logical_id)
        if not self.terraform_path:
            yield f"[terraform-adapter fallback] destroy {logical_id}"
            yield "DESTROY_COMPLETE"
            return

        env = os.environ.copy()
        env["TF_IN_AUTOMATION"] = "1"

        # Windows env fix for destroy local-exec calls (if any)
        if os.name == "nt":
            env.setdefault("SystemRoot", os.environ.get("SystemRoot", r"C:\Windows"))
            env.setdefault("COMSPEC", os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"))
            system32 = os.path.join(env["SystemRoot"], "System32")
            if system32 not in env.get("PATH", ""):
                env["PATH"] = system32 + os.pathsep + env.get("PATH", "")

        destroy_cmd = _build_terraform_destroy_cmd(self.terraform_path)
        yield from _stream_subprocess(destroy_cmd, cwd=wd, env=env)
        yield "DESTROY_COMPLETE"
        self._cleanup(wd)

    # -------------------------
    # Compat
    # -------------------------
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
        if adapter_id.startswith("terraform-"):
            logical_id = adapter_id.replace("terraform-", "")
        else:
            logical_id = adapter_id
        wd = self._workdir_for(logical_id)
        if not os.path.exists(wd):
            print(f"[terraform_adapter] no workdir found for {logical_id}")
            return False

        # If terraform binary not present, attempt to cleanup DB entry and return
        if not self.terraform_path:
            ok = self.store.delete_instance_by_adapter_id(adapter_id)
            self._cleanup(wd)
            return ok

        destroy_cmd = _build_terraform_destroy_cmd(self.terraform_path)
        rc, out = _run(destroy_cmd, cwd=wd)
        if rc != 0:
            print(f"[terraform_adapter] destroy failed: {out[:200]}...")
            return False

        ok = self.store.delete_instance_by_adapter_id(adapter_id)
        self._cleanup(wd)
        return ok
