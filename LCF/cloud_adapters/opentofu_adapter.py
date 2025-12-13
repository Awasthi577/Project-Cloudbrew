# opentofu_adapter.py
import os
import re
import json
import time
import shutil
import subprocess
import logging
from typing import Dict, Any, Optional, List
from jinja2 import Environment, FileSystemLoader, select_autoescape

from LCF import store, utils
from .schema_manager import SchemaManager
from LCF.auth_utils import _load_config
from LCF.secret_store import SecretStore

logger = logging.getLogger("cloudbrew.adapters.opentofu")

# Directory setup
TOFU_ROOT = os.environ.get("CLOUDBREW_TOFU_ROOT", ".cloudbrew_tofu")
if os.name == "nt" and "CLOUDBREW_TOFU_ROOT" not in os.environ:
    TOFU_ROOT = r"C:\tmp\.cloudbrew_tofu"


class OpenTofuAdapter:
    def __init__(self, db_path: Optional[str] = None):
        self.store = store.SQLiteStore(db_path)
        self.tofu_path = self._find_binary()
        self.schema_mgr = SchemaManager(work_dir=TOFU_ROOT)

        # Run a quick GC at init to avoid accumulation of very old workdirs
        try:
            self.gc_old_workdirs(max_age_hours=int(os.environ.get("CLOUDBREW_GC_HOURS", "72")))
        except Exception:
            logger.debug("GC on init failed or skipped.")

        try:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            template_dir = os.path.join(base_dir, "templates")

            if os.path.exists(template_dir):
                self.jinja_env = Environment(
                    loader=FileSystemLoader(template_dir),
                    autoescape=select_autoescape(),
                    trim_blocks=True,
                    lstrip_blocks=True,
                )
            else:
                self.jinja_env = None
        except Exception as e:
            logger.error(f"Failed to init Jinja2: {e}")
            self.jinja_env = None

        # Load CloudBrew credentials for provider authentication
        self._setup_cloudbrew_credentials()

    def _setup_cloudbrew_credentials(self):
        """Set up environment variables with CloudBrew credentials for provider authentication."""
        try:
            config = _load_config()
            if not config:
                logger.debug("No CloudBrew config found")
                return
            
            creds = config.get("creds", {})
            
            # Set up AWS credentials if available
            if creds.get("aws"):
                aws_creds = creds["aws"]
                access_key = aws_creds.get("access_key_id")
                
                if access_key:
                    # Set AWS credentials in environment
                    os.environ["AWS_ACCESS_KEY_ID"] = access_key
                    
                    # Retrieve secret key from secure storage
                    secret_store = SecretStore()
                    secret_key = secret_store.retrieve_secret("aws_secret_key")
                    if secret_key:
                        os.environ["AWS_SECRET_ACCESS_KEY"] = secret_key
                    
                    # Set region if available
                    region = aws_creds.get("region")
                    if region:
                        os.environ["AWS_DEFAULT_REGION"] = region
                    
                    logger.info("AWS credentials loaded from CloudBrew config")
            
            # Set up GCP credentials if available
            if creds.get("gcp"):
                gcp_creds = creds["gcp"]
                sa_path = gcp_creds.get("service_account_path")
                
                if sa_path and os.path.exists(sa_path):
                    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_path
                    logger.info("GCP credentials loaded from CloudBrew config")
            
            # Set up Azure credentials if available
            if creds.get("azure"):
                azure_creds = creds["azure"]
                tenant_id = azure_creds.get("tenant_id")
                client_id = azure_creds.get("client_id")
                
                if tenant_id and client_id:
                    os.environ["ARM_TENANT_ID"] = tenant_id
                    os.environ["ARM_CLIENT_ID"] = client_id
                    
                    # Retrieve client secret from secure storage
                    secret_store = SecretStore()
                    client_secret = secret_store.retrieve_secret("azure_client_secret")
                    if client_secret:
                        os.environ["ARM_CLIENT_SECRET"] = client_secret
                    
                    # Set subscription if available
                    subscription_id = azure_creds.get("subscription_id")
                    if subscription_id:
                        os.environ["ARM_SUBSCRIPTION_ID"] = subscription_id
                    
                    logger.info("Azure credentials loaded from CloudBrew config")
                    
        except Exception as e:
            logger.error(f"Failed to load CloudBrew credentials: {e}")

    def _find_binary(self) -> str:
        path = (
            os.environ.get("CLOUDBREW_OPENTOFU_BIN")
            or shutil.which("tofu")
            or shutil.which("opentofu")
        )
        return path or ""

    def _workdir_for(self, logical_id: str) -> str:
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "-", logical_id)
        path = os.path.join(TOFU_ROOT, safe_name)
        os.makedirs(path, exist_ok=True)
        return path

    def _cleanup(self, workdir: str) -> None:
        for file in ["plan.tfplan", "spec.json"]:
            try:
                os.remove(os.path.join(workdir, file))
            except OSError:
                pass

    def _ensure_azure_cli(self, provider: str) -> str:
        # Placeholder — kept for compatibility with other code paths.
        return ""

    def _get_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        env["TF_IN_AUTOMATION"] = "1"
        return env

    # -------------------------
    # HCL Rendering Helpers
    # -------------------------
    def _render_hcl_field_python(self, key: str, value: Any, schema: Optional[Dict[str, Any]], depth=1) -> str:
        """
        Render a single field/key using a Python-driven renderer.
        Uses schema.get('block_types') for nested blocks (correct OpenTofu schema key).
        """
        indent = "  " * depth
        blocks = schema.get("block_types", {}) if schema else {}

        # Special handling for AWS S3 bucket versioning (common case)
        if key == "versioning" and isinstance(value, dict):
            # AWS S3 bucket versioning should be rendered as a block, not an attribute
            return self._render_block_body("versioning", value, depth)

        if key in blocks:
            # nested block type
            if isinstance(value, list):
                return "\n".join([self._render_block_body(key, item, depth) for item in value])
            return self._render_block_body(key, value, depth)

        if isinstance(value, dict):
            lines = [f'{indent}{key} = {{']
            for sub_k, sub_v in value.items():
                # use JSON dump to correctly quote/escape
                lines.append(f'{indent}  "{sub_k}" = {json.dumps(sub_v)}')
            lines.append(f'{indent}}}')
            return "\n".join(lines)
        return f'{indent}{key} = {json.dumps(value)}'

    def _render_block_body(self, key: str, value: Any, depth: int) -> str:
        indent = "  " * depth
        lines = [f"{indent}{key} {{"]
        if isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, dict):
                    lines.append(f'{indent}  {k} = {{')
                    for sk, sv in v.items():
                        lines.append(f'{indent}    "{sk}" = {json.dumps(sv)}')
                    lines.append(f'{indent}  }}')
                else:
                    lines.append(f'{indent}  {k} = {json.dumps(v)}')
        elif isinstance(value, list):
            # list of primitives or dicts
            for item in value:
                if isinstance(item, dict):
                    lines.append(self._render_block_body(key, item, depth + 1))
                else:
                    lines.append(f'{indent}  {json.dumps(item)}')
        lines.append(f"{indent}}}")
        return "\n".join(lines)

    def _alias_and_defaults(self, spec: Dict[str, Any], resource_type: str, provider: str) -> Dict[str, Any]:
        """
        Apply common alias mappings and safe defaults. Returns modified spec copy.
        """
        s = dict(spec)  # shallow copy
        # Remove meta fields that shouldn't appear in HCL
        for forbidden in ("name", "_resolver_meta", "_provider_hint"):
            s.pop(forbidden, None)

        p = (provider or "").lower()

        # ---------- AWS ----------
        if p.startswith("aws") or p.startswith("amazon"):
            if "image" in s and "ami" not in s:
                s["ami"] = s.pop("image")
            else:
                s.pop("image", None)
            if "size" in s and "instance_type" not in s:
                s["instance_type"] = s.pop("size")
            else:
                s.pop("size", None)

        # ---------- GOOGLE ----------
        elif p.startswith("google") or p.startswith("gcp"):
            if "image" in s and "boot_disk" not in s:
                s["boot_disk"] = {"initialize_params": {"image": s.pop("image")}}
            else:
                s.pop("image", None)
            if "size" in s and "machine_type" not in s:
                s["machine_type"] = s.pop("size")

        # ---------- AZURE ----------
        elif p.startswith("azurerm") or p.startswith("azure"):
            if "image" in s and "source_image_reference" not in s:
                s["source_image_reference"] = {"sku": s.pop("image")}
            else:
                s.pop("image", None)
            # Azure often keeps "size" as-is

        # Normalize tags if provided as JSON string
        if isinstance(s.get("tags"), str):
            try:
                s["tags"] = json.loads(s["tags"])
            except Exception:
                pass

        return s

    # -------------------------
    # Normalization
    # -------------------------
    def _normalize_spec_for_provider(self, spec: Dict[str, Any], provider: str) -> Dict[str, Any]:
        """
        Map friendly/CLI aliases to provider-native attribute names and
        remove keys that should not appear inside the resource body
        (e.g., 'name' is represented by the resource label).
        """
        s = dict(spec)  # shallow copy to avoid mutating caller

        # Always remove runtime/internal keys that must not be inside resource body
        for forbidden in ("name", "_resolver_meta", "_provider_hint"):
            s.pop(forbidden, None)

        p = (provider or "").lower()

        # Generic mapping common across clouds
        if p in ("aws", "amazon", "hashicorp/aws", "aws_instance"):
            # image -> ami
            if "ami" not in s:
                if "image" in s:
                    s["ami"] = s.pop("image")
            else:
                s.pop("image", None)

            # size -> instance_type
            if "instance_type" not in s:
                if "size" in s:
                    s["instance_type"] = s.pop("size")
            else:
                s.pop("size", None)

            if isinstance(s.get("tags"), str):
                try:
                    s["tags"] = json.loads(s["tags"])
                except Exception:
                    pass

        elif p in ("gcp", "google", "hashicorp/google", "google_compute_instance"):
            if "image" in s and "boot_disk" not in s:
                s["boot_disk"] = {"initialize_params": {"image": s.pop("image")}}
            else:
                s.pop("image", None)
            if "size" in s and "machine_type" not in s:
                s["machine_type"] = s.pop("size")
            else:
                s.pop("size", None)

        elif p in ("azure", "azurerm", "hashicorp/azurerm", "azurerm_linux_virtual_machine"):
            if "image" in s and "source_image_reference" not in s:
                s["source_image_reference"] = {"publisher": "", "offer": "", "sku": s.pop("image")}
            else:
                s.pop("image", None)

        return s

    def default_value_for_type(t):
        if isinstance(t, list) and t and t[0] == "list":
            return "[]"
        if isinstance(t, list) and t and t[0] == "map":
            return "{}"
        if t == "string":
            return '"AUTO"'
        if t == "number":
            return "0"
        if t == "bool":
            return "false"
        return '"AUTO"'

    def build_hcl_from_schema(provider: str, resource: str, schema: dict, user_inputs: dict=None):
        """
        Generate a minimal valid HCL resource using only required arguments.
        """
        user_inputs = user_inputs or {}

        block = schema["block"]
        attrs = block.get("attributes", {})
        blocks = block.get("block_types", {})

        lines = [f'resource "{resource}" "{provider}" {{']

        # Required attributes
        for name, spec in attrs.items():
            if spec.get("required"):
                if name in user_inputs:
                    lines.append(f'  {name} = {user_inputs[name]}')
                else:
                    val = OpenTofuAdapter.default_value_for_type(spec.get("type"))
                    lines.append(f'  {name} = {val}')

        # Required nested blocks
        for blk, blk_spec in blocks.items():
            if blk_spec.get("min_items", 0) > 0:
                lines.append(f'  {blk} {{')
                nested = blk_spec["block"].get("attributes", {})
                for aname, aspec in nested.items():
                    if aspec.get("required"):
                        if blk in user_inputs and aname in user_inputs[blk]:
                            lines.append(f'    {aname} = {user_inputs[blk][aname]}')
                        else:
                            val = OpenTofuAdapter.default_value_for_type(aspec.get("type"))
                            lines.append(f'    {aname} = {val}')
                lines.append("  }")

        lines.append("}")
        return "\n".join(lines)

    # -------------------------
    # Schema-driven renderer
    # -------------------------
    def _render_hcl_from_schema(self, resource_type: str, logical_name: str, spec: Dict[str, Any], schema: Optional[Dict[str, Any]]) -> str:
        """
        Render HCL for a single resource using provider schema information.
        """
        safe_name = re.sub(r"[^A-Za-z0-9_]", "_", logical_name.replace(" ", "_"))
        if safe_name and safe_name[0].isdigit():
            safe_name = f"res_{safe_name}"

        provider = spec.get("provider", "")
        spec_local = self._alias_and_defaults(spec, resource_type, provider)

        block = (schema or {}).get("block", {}) if schema else {}
        schema_attrs = block.get("attributes", {}) if block else {}
        block_types = block.get("block_types", {}) if block else {}

        lines: List[str] = [f'resource "{resource_type}" "{safe_name}" {{']

        # Render attributes present in spec that are also in schema (deterministic order)
        for attr in sorted(schema_attrs.keys()):
            if attr in spec_local:
                try:
                    lines.append(self._render_hcl_field_python(attr, spec_local[attr], schema))
                except Exception as e:
                    logger.debug("Failed to render attr %s: %s", attr, e)
                    lines.append(f'  # Could not render {attr}: {e}')

        # Render block_types if present in spec
        for block_name in sorted(block_types.keys()):
            if block_name in spec_local:
                try:
                    lines.append(self._render_hcl_field_python(block_name, spec_local[block_name], schema))
                except Exception as e:
                    logger.debug("Failed to render block %s: %s", block_name, e)
                    lines.append(f'  # Could not render block {block_name}: {e}')

        # Render any remaining user-provided keys (best-effort)
        for k in sorted(spec_local.keys()):
            if k in schema_attrs or k in block_types:
                continue
            if k.startswith("_"):
                continue
            try:
                lines.append(self._render_hcl_field_python(k, spec_local[k], schema=None))
            except Exception:
                lines.append(f'  # Skipped rendering of {k}')

        lines.append("}")
        return "\n".join(lines)

    # -------------------------
    # HCL Generator
    # -------------------------
    def _generate_hcl_legacy(self, logical_id: str, spec: Dict[str, Any]) -> str:
        return f"# Legacy fallback for {logical_id}\n"

    def _generate_hcl(self, logical_id: str, spec: Dict[str, Any]) -> str:
        if "_hcl_override" in spec:
            return spec["_hcl_override"]
        
        resource_type = spec.get("type", "null_resource")
        provider = spec.get("provider", "aws").lower()

        # Normalize spec early so schema/templating sees provider-native fields
        spec_for_render = self._normalize_spec_for_provider(spec, provider)

        # Schema (if available) helps rendering nested blocks
        schema = None
        try:
            schema = self.schema_mgr.get(resource_type)
        except Exception:
            schema = None

        # Generate provider header for common providers
        header = ""
        if provider in ("aws", "amazon"):
            region = spec_for_render.get("region") or os.environ.get("AWS_REGION") or "us-east-1"
            header = f"""
terraform {{
  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }}
  }}
}}

provider "aws" {{
  region = "{region}"
}}
"""
        elif provider in ("azure", "azurerm"):
            header = """
terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
  features {}
}
"""
        elif provider in ("google", "gcp", "hashicorp/google"):
            region = spec_for_render.get("region") or os.environ.get("GOOGLE_REGION", "us-central1")
            header = f"""
terraform {{
  required_providers {{
    google = {{
      source = "hashicorp/google"
      version = ">= 4.0"
    }}
  }}
}}

provider "google" {{
  region = "{region}"
}}
"""

        # Provider/resource-specific gentle fixes (example)
        clean_spec = {k: v for k, v in spec_for_render.items() if not k.startswith("_") and k not in {"type", "provider", "region"}}

        if resource_type == "aws_dynamodb_table":
            if "billing_mode" not in clean_spec:
                clean_spec["billing_mode"] = "PAY_PER_REQUEST"
            if "hash_key" in clean_spec and "attribute" not in clean_spec:
                clean_spec["attribute"] = [{"name": clean_spec["hash_key"], "type": "S"}]

        # If template exists for this resource type, use it (templates live in templates/ directory)
        template_file = self._find_template_file(resource_type)
        if template_file:
            return header + "\n" + self._render_jinja_template(template_file, logical_id, clean_spec, resource_type, schema=schema)

        # If schema exists, use schema-driven renderer
        if schema:
            try:
                return header + "\n" + self._render_hcl_from_schema(resource_type, logical_id, {"provider": provider, **clean_spec}, schema)
            except Exception as e:
                logger.exception("Schema-driven rendering failed, falling back: %s", e)

        # Otherwise, fallback to generic template
        return header + "\n" + self._render_jinja_template("generic_resource_safe.tf.j2", logical_id, clean_spec, resource_type, schema=schema)

    def _find_template_file(self, resource_type: str) -> Optional[str]:
        if not self.jinja_env:
            return None
        expected = f"{resource_type}.tf.j2"
        try:
            return expected if expected in self.jinja_env.list_templates() else None
        except Exception:
            return None

    def _render_jinja_template(self, template_name: str, logical_id: str, spec: Dict[str, Any], resource_type: str, schema: Any = None) -> str:
        if not self.jinja_env:
            return "# ERROR: Jinja2 not initialized."

        clean_name = re.sub(r"[^A-Za-z0-9_]", "_", logical_id.replace(" ", "_"))
        if clean_name and clean_name[0].isdigit():
            clean_name = f"res_{clean_name}"

        def python_renderer(k, v):
            return self._render_hcl_field_python(k, v, schema)

        try:
            template = self.jinja_env.get_template(template_name)
            context = {
                "resource_type": resource_type,
                "name": clean_name,
                "spec": spec,
                "schema": schema,
                "render_field": python_renderer,
            }
            return template.render(**context)
        except Exception as e:
            logger.exception("Jinja render failed")
            return f"# ERROR: Failed to render template '{template_name}': {e}"

    # -------------------------
    # Workdir garbage collector
    # -------------------------
    def gc_old_workdirs(self, max_age_hours: int = 72) -> None:
        """Remove workdirs older than max_age_hours. Safe guard to avoid deleting active dirs."""
        try:
            now = time.time()
            cutoff = now - (max_age_hours * 3600)
            root = TOFU_ROOT
            if not os.path.exists(root):
                return
            for name in os.listdir(root):
                path = os.path.join(root, name)
                try:
                    if not os.path.isdir(path):
                        continue
                    mtime = os.path.getmtime(path)
                    # do not remove if recently modified (race safeguard)
                    if mtime < cutoff:
                        logger.info("GC removing old workspace: %s (age %.1f hours)", path, (now - mtime) / 3600.0)
                        shutil.rmtree(path, ignore_errors=True)
                except Exception:
                    logger.exception("Error during GC for %s", path)
        except Exception:
            logger.exception("gc_old_workdirs failure")

    # -------------------------
    # Public API
    # -------------------------
    def create_instance(self, logical_id: str, spec: Dict[str, Any], plan_only: bool = False) -> Dict[str, Any]:
        """
        Synchronous implementation of create_instance to satisfy CLI requirements.
        Generates HCL, initializes OpenTofu, and runs Plan/Apply.
        """
        try:
            # 1. Generate HCL & Prepare Workspace
            wd = self._workdir_for(logical_id)
            
            # Clean up any existing workspace to avoid stale HCL files
            if os.path.exists(wd):
                try:
                    shutil.rmtree(wd)
                    logger.info(f"Cleaned up existing workspace: {wd}")
                except Exception as e:
                    logger.warning(f"Failed to clean workspace {wd}: {e}")
                    # Continue anyway - we'll overwrite the files
            
            # Generate fresh HCL
            hcl = self._generate_hcl(logical_id, spec)
            
            # Ensure workspace directory exists
            os.makedirs(wd, exist_ok=True)

            # Write configuration
            with open(os.path.join(wd, "main.tf"), "w", encoding="utf-8") as f:
                f.write(hcl)

            # 2. Run 'tofu init' (if needed)
            if not os.path.exists(os.path.join(wd, ".terraform")):
                if not self.tofu_path:
                    return {"success": False, "error": "OpenTofu binary not found. Set CLOUDBREW_OPENTOFU_BIN or install 'tofu'."}
                init_res = subprocess.run([self.tofu_path, "init", "-no-color"], cwd=wd, capture_output=True, text=True)
                if init_res.returncode != 0:
                    return {"success": False, "error": f"Init failed: {init_res.stderr or init_res.stdout}"}

            # 3. Construct Command (Plan vs Apply)
            cmd = [self.tofu_path]
            if plan_only:
                cmd.extend(["plan", "-no-color"])
            else:
                cmd.extend(["apply", "-auto-approve", "-no-color"])

            # 4. Execute with timeout and better error handling
            try:
                # Add timeout to prevent hanging
                proc = subprocess.run(
                    cmd, 
                    cwd=wd, 
                    capture_output=True, 
                    text=True,
                    timeout=300  # 5 minute timeout
                )

                # Debug output
                logger.info(f"OpenTofu command: {' '.join(cmd)}")
                logger.info(f"OpenTofu stdout: {proc.stdout[:500]}...")  # First 500 chars
                logger.info(f"OpenTofu stderr: {proc.stderr[:500]}...")  # First 500 chars
                logger.info(f"OpenTofu return code: {proc.returncode}")

                if proc.returncode == 0:
                    # Optionally cleanup workspace after successful apply to avoid disk litter:
                    try:
                        if not os.environ.get("CLOUDBREW_KEEP_WORKDIR"):
                            shutil.rmtree(wd, ignore_errors=True)
                        else:
                            logger.info("Keeping workspace for debugging (CLOUDBREW_KEEP_WORKDIR set): %s", wd)
                    except Exception:
                        logger.exception("Failed to remove workspace %s", wd)

                    return {
                        "success": True,
                        "output": proc.stdout,
                        "path": wd,
                    }
                else:
                    error_msg = proc.stderr or proc.stdout or "Unknown error"
                    return {
                        "success": False,
                        "error": f"OpenTofu failed with return code {proc.returncode}: {error_msg}",
                        "output": proc.stdout,
                    }
            except subprocess.TimeoutExpired:
                logger.error(f"OpenTofu command timed out after 300 seconds: {' '.join(cmd)}")
                return {
                    "success": False,
                    "error": f"OpenTofu command timed out after 300 seconds. Workspace preserved at: {wd}",
                    "output": "Command timed out"
                }
            except Exception as e:
                logger.exception(f"Exception running OpenTofu command: {' '.join(cmd)}")
                return {
                    "success": False,
                    "error": f"Exception running OpenTofu: {str(e)}",
                    "output": ""
                }

        except Exception as e:
            logger.exception("Exception in create_instance")
            return {"success": False, "error": str(e)}

    def destroy_instance(self, adapter_id: str) -> bool:
        """Destroys an instance managed by OpenTofu."""
        if not self.tofu_path:
            logger.error("OpenTofu binary not found. Cannot destroy.")
            return False

        logical_id = adapter_id.split("-", 1)[-1]
        wd = self._workdir_for(logical_id)

        # Check if a state file exists before trying to destroy
        if not os.path.exists(os.path.join(wd, "terraform.tfstate")):
            logger.warning(f"No state file found for {adapter_id} at {wd}. Assuming success for cleanup.")
            try:
                shutil.rmtree(wd, ignore_errors=True)
            except Exception:
                pass
            return True

        # Check for lock before destroy
        if os.path.exists(os.path.join(wd, ".terraform.tfstate.lock.info")):
            logger.error(f"Cannot destroy {adapter_id}: State file is locked.")
            return False

        cmd = [self.tofu_path, "destroy", "-auto-approve", "-no-color"]

        try:
            proc = subprocess.run(cmd, cwd=wd, capture_output=True, text=True, timeout=300)

            if proc.returncode == 0:
                logger.info(f"Destroy successful for {adapter_id}")
                shutil.rmtree(wd, ignore_errors=True)
                return True
            else:
                logger.error(f"Destroy failed for {adapter_id}. Error: {proc.stderr or proc.stdout}")
                return False

        except Exception as e:
            logger.exception(f"Error during destruction of {adapter_id}: {e}")
            return False

    def check_drift(self, logical_id: str) -> Dict[str, Any]:
        """
        Runs 'tofu plan -detailed-exitcode' to check for drift.
        Exit codes: 0 = No changes, 1 = Error, 2 = Drift/Changes pending.
        """
        if not self.tofu_path:
            return {"drifted": None, "msg": "OpenTofu binary not found."}

        wd = self._workdir_for(logical_id)

        # 1. Check for State Lock (Common failure point)
        lock_file = os.path.join(wd, ".terraform.tfstate.lock.info")
        if os.path.exists(lock_file):
            return {
                "drifted": None,
                "msg": f"State lock detected at {lock_file}. Please remove manually or wait.",
            }

        # 2. Ensure workspace is initialized
        if not os.path.exists(os.path.join(wd, ".terraform")):
            init_res = subprocess.run([self.tofu_path, "init", "-no-color"], cwd=wd, capture_output=True, text=True)
            if init_res.returncode != 0:
                return {"drifted": None, "msg": f"Init failed: {init_res.stderr or init_res.stdout}"}

        # 3. Command with exit code flag
        cmd = [self.tofu_path, "plan", "-detailed-exitcode", "-no-color"]

        try:
            proc = subprocess.run(cmd, cwd=wd, capture_output=True, text=True, timeout=120)

            # Exit code 0: No changes (No Drift)
            if proc.returncode == 0:
                return {"drifted": False, "summary": "No drift detected.", "details": proc.stdout}

            # Exit code 2: Changes are required (Drift Detected)
            elif proc.returncode == 2:
                return {"drifted": True, "summary": "Drift detected (changes are pending in plan).", "details": proc.stdout}

            # Exit code 1: Error during execution
            elif proc.returncode == 1:
                # Capture BOTH stdout and stderr to ensure we see the error
                error_output = (proc.stderr or "").strip()
                if not error_output:
                    error_output = (proc.stdout or "").strip()
                if not error_output:
                    error_output = "Unknown OpenTofu error (Exit Code 1)"

                return {"drifted": None, "msg": error_output, "details": proc.stdout}

            # Other exit code
            else:
                return {
                    "drifted": None,
                    "msg": f"OpenTofu terminated unexpectedly (Code {proc.returncode})",
                    "details": proc.stderr or proc.stdout,
                }

        except Exception as e:
            logger.exception("Error running drift check")
            return {"drifted": None, "msg": f"Error running drift check: {e}"}

    def apply_plan(self, plan_id: str, **kwargs) -> Dict[str, Any]:
        """
        Applies a previously saved plan file (plan_id is the path to the .tfplan file).
        """
        if not self.tofu_path:
            return {"success": False, "error": "OpenTofu binary not found."}

        plan_path = plan_id

        if not os.path.exists(plan_path):
            return {"success": False, "error": f"Plan file not found: {plan_path}"}

        wd = os.path.dirname(plan_path)

        cmd = [self.tofu_path, "apply", "-no-color", plan_path]

        try:
            proc = subprocess.run(cmd, cwd=wd, capture_output=True, text=True, timeout=600)

            if proc.returncode == 0:
                return {"success": True, "output": proc.stdout, "path": wd}
            else:
                return {"success": False, "error": proc.stderr or proc.stdout, "output": proc.stdout}

        except Exception as e:
            logger.exception("Error applying plan")
            return {"success": False, "error": str(e)}