"""
OpenTofu Adapter for CloudBrew.

This adapter provides a drop-in replacement for Terraform, using the 'tofu' binary.
It manages the lifecycle of infrastructure resources via OpenTofu (init, plan, apply, destroy).

Key Features:
- Automatic caching of resource mappings.
- Streaming output for real-time CLI feedback.
- Robust subprocess handling with timeouts.
- Thread-safe SQLite connections.
"""

from __future__ import annotations
import os
import re
import json
import subprocess
import shutil
import time
import sqlite3
import logging
from typing import Dict, Any, Optional, Generator, List, Tuple

from LCF import store

# --- Configuration & Constants ---
logger = logging.getLogger("cloudbrew.adapters.opentofu")

# Directory setup
TOFU_ROOT = os.environ.get("CLOUDBREW_TOFU_ROOT", ".cloudbrew_tofu")
if os.name == "nt" and "CLOUDBREW_TOFU_ROOT" not in os.environ:
    # Use a temp directory on Windows to avoid long path issues if needed
    TOFU_ROOT = r"C:\tmp\.cloudbrew_tofu"

CACHE_DIR = ".cloudbrew_cache"
CACHE_DB_PATH = os.path.join(CACHE_DIR, "resources.db")

# Ensure directories exist
os.makedirs(TOFU_ROOT, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# Maps for simplified DSL -> Cloud Specifics
AWS_IMAGE_MAP = {
    "ubuntu-22.04": "ami-0a63f6f9f6f9abcde",  # Example placeholder
    "ubuntu-20.04": "ami-052efd3df9dad4825"
}
AWS_SIZE_MAP = {
    "small": "t3.micro",
    "medium": "t3.medium",
    "large": "t3.large",
    "xlarge": "t3.xlarge"
}

# --- Helper Functions ---

def _build_tofu_cmd(action: str, tofu_path: str, flags: Optional[List[str]] = None, targets: Optional[List[str]] = None) -> List[str]:
    """Constructs a secure and correct OpenTofu CLI command."""
    cmd = [tofu_path, action]
    
    # Standard flags for automation
    defaults = ["-no-color", "-input=false"]
    cmd.extend(defaults)

    # Action specific defaults
    if action in ("apply", "destroy"):
        cmd.append("-auto-approve")
    
    if flags:
        cmd.extend(flags)
        
    if targets:
        cmd.extend(targets)
        
    return cmd

def _ensure_cache_db() -> sqlite3.Connection:
    """Ensures the cache database exists and returns a connection."""
    conn = sqlite3.connect(CACHE_DB_PATH, check_same_thread=False)
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

def _stream_subprocess(cmd: List[str], cwd: str, env: Dict[str, str], timeout: int = 600) -> Generator[str, None, None]:
    """
    Runs a subprocess and yields stdout line-by-line. 
    Raises RuntimeError on non-zero exit code.
    """
    cmd_str = " ".join(cmd)
    logger.debug(f"Executing: {cmd_str} in {cwd}")

    try:
        proc = subprocess.Popen(
            cmd, 
            cwd=cwd, 
            env=env,
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT, 
            text=True, 
            bufsize=1
        )
    except FileNotFoundError:
        err = f"Binary not found: {cmd[0]}"
        logger.error(err)
        yield err
        raise RuntimeError(err)

    output_buffer = []
    start_time = time.time()

    try:
        if proc.stdout:
            for line in proc.stdout:
                line = line.rstrip()
                output_buffer.append(line)
                yield line
                
                # Basic timeout check during execution
                if time.time() - start_time > timeout:
                    proc.kill()
                    raise TimeoutError(f"Command timed out after {timeout}s")

        proc.wait(timeout=timeout)
        
        if proc.returncode != 0:
            error_msg = f"Command failed (Exit: {proc.returncode})"
            logger.error(f"{error_msg}\nLog:\n" + "\n".join(output_buffer[-20:])) # Log last 20 lines
            raise RuntimeError(error_msg)

    except subprocess.TimeoutExpired:
        proc.kill()
        yield "ERROR: Operation timed out."
        raise RuntimeError(f"Command timed out: {cmd_str}")
    finally:
        if proc.stdout:
            proc.stdout.close()

# --- Adapter Implementation ---

class OpenTofuAdapter:
    def __init__(self, db_path: Optional[str] = None):
        self.store = store.SQLiteStore(db_path)
        self.tofu_path = self._find_binary()
        self.conn = _ensure_cache_db()

    def _find_binary(self) -> str:
        """Locates the OpenTofu binary."""
        path = os.environ.get("CLOUDBREW_OPENTOFU_BIN") or shutil.which("tofu") or shutil.which("opentofu")
        if not path:
            logger.warning("OpenTofu binary ('tofu') not found in PATH.")
            return ""
        return path

    def _workdir_for(self, logical_id: str) -> str:
        """Creates a safe working directory for a specific resource ID."""
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '-', logical_id)
        path = os.path.join(TOFU_ROOT, safe_name)
        os.makedirs(path, exist_ok=True)
        return path

    def _cleanup(self, workdir: str) -> None:
        """Cleans up temporary plan files and specs, keeps state."""
        for file in ["plan.tfplan", "spec.json"]:
            try:
                os.remove(os.path.join(workdir, file))
            except OSError:
                pass

    def _get_env(self) -> Dict[str, str]:
        """Prepares the environment variables for OpenTofu."""
        env = os.environ.copy()
        env["TF_IN_AUTOMATION"] = "1"
        env["TOFU_IN_AUTOMATION"] = "1"
        
        # Windows-specific path fixes for local-exec
        if os.name == "nt":
            env.setdefault("SystemRoot", r"C:\Windows")
            env.setdefault("COMSPEC", r"C:\Windows\System32\cmd.exe")
            sys32 = os.path.join(env["SystemRoot"], "System32")
            if sys32 not in env.get("PATH", ""):
                env["PATH"] = f"{sys32}{os.pathsep}{env.get('PATH', '')}"
        
        return env

    def _generate_hcl(self, logical_id: str, spec: Dict[str, Any]) -> str:
        """Generates HCL based on the provider and spec."""
        provider = (spec.get("provider") or "aws").lower()
        name = logical_id.replace("-", "_")
        
        if provider == "aws":
            region = spec.get("region", "us-east-1")
            ami = AWS_IMAGE_MAP.get(spec.get("image", "ubuntu-22.04"), "ami-0a63f6f9f6f9abcde")
            instance_type = AWS_SIZE_MAP.get(spec.get("size", "small"), "t3.micro")
            
            return f"""
provider "aws" {{
  region = "{region}"
}}

resource "aws_instance" "{name}" {{
  ami           = "{ami}"
  instance_type = "{instance_type}"
  tags = {{
    Name = "{logical_id}"
    ManagedBy = "CloudBrew-OpenTofu"
  }}
}}
"""
        else:
            # Generic fallback for other providers or testing
            return f"""
resource "null_resource" "{name}" {{
  triggers = {{
    created_at = "{time.time()}"
  }}
  provisioner "local-exec" {{
    command = "echo 'Creating {logical_id} via OpenTofu (Provider: {provider})'"
  }}
}}
"""

    # --- Streaming Methods ---

    def stream_create_instance(self, logical_id: str, spec: Dict[str, Any], plan_only: bool = False) -> Generator[str, None, None]:
        """
        Orchestrates the Init -> Plan -> Apply cycle, yielding output.
        """
        if not self.tofu_path:
            yield "ERROR: OpenTofu binary not found. Cannot proceed."
            raise RuntimeError("OpenTofu binary missing")

        wd = self._workdir_for(logical_id)
        env = self._get_env()

        # 1. Write HCL
        hcl_content = self._generate_hcl(logical_id, spec)
        with open(os.path.join(wd, "main.tf"), "w", encoding="utf-8") as f:
            f.write(hcl_content)
        
        with open(os.path.join(wd, "spec.json"), "w", encoding="utf-8") as f:
            json.dump(spec, f)

        # 2. Init
        yield "Initializing OpenTofu..."
        yield from _stream_subprocess(
            _build_tofu_cmd("init", self.tofu_path), 
            cwd=wd, env=env
        )

        # 3. Plan
        plan_file = os.path.abspath(os.path.join(wd, "plan.tfplan"))
        yield "Generating Plan..."
        yield from _stream_subprocess(
            _build_tofu_cmd("plan", self.tofu_path, flags=["-out", plan_file, "-detailed-exitcode"]), 
            cwd=wd, env=env
        )
        
        yield f"PLAN_SAVED:{plan_file}"
        
        if plan_only:
            # We don't cleanup here so the plan can be inspected/applied later
            return

        # 4. Apply
        yield "Applying Plan..."
        yield from _stream_subprocess(
            _build_tofu_cmd("apply", self.tofu_path, targets=[plan_file]), 
            cwd=wd, env=env
        )
        
        yield "APPLY_COMPLETE"
        self._cleanup(wd)

    def stream_destroy_instance(self, logical_id: str) -> Generator[str, None, None]:
        """Streams the destruction process."""
        if not self.tofu_path:
            yield "ERROR: OpenTofu binary missing."
            return

        wd = self._workdir_for(logical_id)
        if not os.path.exists(os.path.join(wd, "main.tf")):
            yield f"No resource definition found for {logical_id}"
            return

        env = self._get_env()
        
        yield f"Destroying {logical_id}..."
        yield from _stream_subprocess(
            _build_tofu_cmd("destroy", self.tofu_path), 
            cwd=wd, env=env
        )
        
        yield "DESTROY_COMPLETE"
        # Optional: Aggressive cleanup of the whole dir after destroy
        # shutil.rmtree(wd, ignore_errors=True)

    def stream_apply_plan(self, plan_path: str) -> Generator[str, None, None]:
        """Applies a previously generated plan file."""
        if not os.path.exists(plan_path):
            raise FileNotFoundError(f"Plan file not found: {plan_path}")
            
        wd = os.path.dirname(plan_path)
        env = self._get_env()

        yield f"Applying existing plan: {plan_path}"
        yield from _stream_subprocess(
            _build_tofu_cmd("apply", self.tofu_path, targets=[plan_path]), 
            cwd=wd, env=env
        )
        yield "APPLY_COMPLETE"
        self._cleanup(wd)

    # --- Protocol Implementation (Public API) ---

    def create_instance(self, logical_id: str, spec: Dict[str, Any], plan_only: bool = False) -> Dict[str, Any]:
        """Protocol compliant creation method."""
        try:
            gen = self.stream_create_instance(logical_id, spec, plan_only=plan_only)
            
            # Consume generator to execute
            last_output = ""
            plan_path = None
            
            for line in gen:
                # print(line) # Uncomment if direct stdout is desired alongside return
                last_output = line
                if line.startswith("PLAN_SAVED:"):
                    plan_path = line.split(":", 1)[1]

            result = {
                "success": True,
                "adapter_id": f"opentofu-{logical_id}",
                "output": last_output
            }
            
            if plan_only and plan_path:
                result["plan_id"] = plan_path
                
            return result
            
        except Exception as e:
            logger.error(f"Create instance failed for {logical_id}: {e}")
            return {"success": False, "error": str(e)}

    def destroy_instance(self, adapter_id: str) -> bool:
        """Protocol compliant destroy method."""
        # Strip prefix if present
        logical_id = adapter_id.replace("opentofu-", "").replace("terraform-", "")
        
        try:
            # We don't strictly need to stream here for the boolean return API
            # but we run it to ensure the process completes.
            gen = self.stream_destroy_instance(logical_id)
            for _ in gen: pass 
            
            # Update local store
            self.store.delete_instance_by_adapter_id(adapter_id)
            return True
        except Exception as e:
            logger.error(f"Destroy failed for {adapter_id}: {e}")
            return False

    def plan(self, logical_id: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Protocol compliant plan method."""
        return self.create_instance(logical_id, spec, plan_only=True)

    def apply_plan(self, plan_id: str, **kwargs) -> Dict[str, Any]:
        """Protocol compliant apply method."""
        try:
            gen = self.stream_apply_plan(plan_id)
            last_output = ""
            for line in gen:
                last_output = line
            return {"success": True, "output": last_output}
        except Exception as e:
            logger.error(f"Apply plan failed for {plan_id}: {e}")
            return {"success": False, "error": str(e)}

    def check_drift(self, logical_id: str) -> Dict[str, Any]:
        """
        Checks for drift by running 'tofu plan -detailed-exitcode'.
        Returns a dict summarizing the drift status.
        """
        wd = self._workdir_for(logical_id)
        if not os.path.exists(os.path.join(wd, "main.tf")):
             return {"drifted": None, "msg": "Resource not managed by OpenTofu"}

        env = self._get_env()
        cmd = _build_tofu_cmd("plan", self.tofu_path, flags=["-detailed-exitcode", "-refresh=true"])
        
        try:
            proc = subprocess.run(
                cmd, cwd=wd, env=env, 
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300
            )
            
            output = proc.stdout
            
            # Exit code 0: No changes (No Drift)
            if proc.returncode == 0:
                 return {"drifted": False, "msg": "Infrastructure is up-to-date."}
            
            # Exit code 2: Succeeded, but there is a diff (Drift Detected)
            elif proc.returncode == 2:
                # Attempt to parse summary line
                match = re.search(r"Plan:\s*(\d+)\s+to add,\s*(\d+)\s+to change,\s*(\d+)\s+to destroy", output)
                summary = match.group(0) if match else "Changes detected"
                return {
                    "drifted": True, 
                    "msg": "Drift detected.", 
                    "summary": summary,
                    "details": output[-1000:] # Return tail of output
                }
            
            # Exit code 1: Error
            else:
                 return {"drifted": None, "msg": "Drift check failed (Error)", "details": output}

        except Exception as e:
            return {"drifted": None, "msg": f"Drift check exception: {str(e)}"}