from __future__ import annotations  

import json
import os
import sys
import click
import time
from pathlib import Path
import copy  
from typing import Optional, List, Dict, Any

import typer
from typer.core import TyperGroup
from LCF.cloud_adapters.tofu_validator import run_tofu_validate
from LCF.cloud_adapters.dynamic_resource_creator import create_resource_with_validation

# Try importing PyYAML for YAML support
try:
    import yaml
except ImportError:
    yaml = None

# -------------------------------------------------------------
# Local Imports
# -------------------------------------------------------------
from LCF.resource_resolver import ResourceResolver
from LCF.dsl_parser import parse_cbdsl 
from LCF.autoscaler import AutoscalerManager, parse_autoscale_string
from LCF.offload.manager import OffloadManager
from LCF.cloud_adapters import pulumi_adapter
from LCF.cloud_adapters.opentofu_adapter import OpenTofuAdapter
from LCF.intelligent_builder import IntelligentBuilder  # NEW
from LCF.provisioning.pipeline import ProvisioningPipeline

# Feature Imports
from LCF.policy_engine import PolicyEngine
from LCF.stack_manager import StackManager
from LCF.intelligent_router import IntelligentRouter
from LCF.pool_manager import WarmPoolManager
from LCF import store
from LCF.auth_utils import ensure_authenticated_for_resource, get_default_provider

# -------------------------------------------------------------
# Constants
# -------------------------------------------------------------
DEFAULT_DB = "cloudbrew.db"
DEFAULT_OFFLOAD_DB = "cloudbrew_offload.db"

# Initialize Intelligent Builder
intelligent_builder = IntelligentBuilder()
provisioning_pipeline = ProvisioningPipeline()


# -------------------------------------------------------------
# Helpers
# -------------------------------------------------------------
def _load_spec(path_str: str) -> Dict[str, Any]:
    """Load a spec from JSON or YAML file."""
    p = Path(path_str)
    if not p.exists():
        # Fallback: check if user provided just the name without extension
        if not path_str.endswith(".json") and not path_str.endswith(".yml"):
            json_p = Path(f"{path_str}.json")
            yml_p = Path(f"{path_str}.yml")
            if json_p.exists(): return _load_spec(str(json_p))
            elif yml_p.exists(): return _load_spec(str(yml_p))
        
        raise typer.BadParameter(f"Spec file not found: {path_str}")

    content = p.read_text(encoding="utf-8")

    if p.suffix == ".cbdsl":
        try:
            return parse_cbdsl(content)
        except Exception as e:
            raise typer.BadParameter(f"Invalid CBDSL in {path_str}: {e}")
    
    # Try YAML if extension matches or if json fails
    if p.suffix in (".yml", ".yaml"):
        if yaml is None:
            raise typer.Exit("PyYAML is missing. Install it to use .yml files: pip install PyYAML")
        try:
            return yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise typer.BadParameter(f"Invalid YAML in {path_str}: {e}")
    
    # Default to JSON
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        # If it wasn't explicitly .yml but failed JSON, try YAML as fallback if installed
        if yaml:
            try:
                return yaml.safe_load(content)
            except Exception:
                pass
        raise typer.BadParameter(f"Invalid JSON in {path_str}: {e}")


def prompt_for_missing_fields(spec: Dict[str, Any], resource_type: str) -> Dict[str, Any]:
    """Deprecated shim: canonical create flow now uses LCF.provisioning.prompt_engine."""
    typer.echo(
        "prompt_for_missing_fields is deprecated and bypassed by the canonical create pipeline.",
        err=True,
    )
    return dict(spec)
    """Load a spec from JSON or YAML file."""
    p = Path(path_str)
    if not p.exists():
        # Fallback: check if user provided just the name without extension
        if not path_str.endswith(".json") and not path_str.endswith(".yml"):
            json_p = Path(f"{path_str}.json")
            yml_p = Path(f"{path_str}.yml")
            if json_p.exists(): return _load_spec(str(json_p))
            elif yml_p.exists(): return _load_spec(str(yml_p))
        
        raise typer.BadParameter(f"Spec file not found: {path_str}")

    content = p.read_text(encoding="utf-8")

    if p.suffix == ".cbdsl":
        try:
            return parse_cbdsl(content)
        except Exception as e:
            raise typer.BadParameter(f"Invalid CBDSL in {path_str}: {e}")
    
    # Try YAML if extension matches or if json fails
    if p.suffix in (".yml", ".yaml"):
        if yaml is None:
            raise typer.Exit("PyYAML is missing. Install it to use .yml files: pip install PyYAML")
        try:
            return yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise typer.BadParameter(f"Invalid YAML in {path_str}: {e}")
    
    # Default to JSON
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        # If it wasn't explicitly .yml but failed JSON, try YAML as fallback if installed
        if yaml:
            try:
                return yaml.safe_load(content)
            except Exception:
                pass
        raise typer.BadParameter(f"Invalid JSON in {path_str}: {e}")

def parse_autoscale_config(autoscale_str: str) -> Dict:
    """Parse autoscaling configuration string"""
    try:
        # Format: min:max@metric:threshold,cooldown
        parts = autoscale_str.split('@')
        if len(parts) != 2:
            raise ValueError("Invalid autoscale format")
        
        scale_part, metric_part = parts
        min_max = scale_part.split(':')
        metric_threshold = metric_part.split(':')
        
        if len(min_max) != 2 or len(metric_threshold) != 2:
            raise ValueError("Invalid autoscale format")
        
        min_size, max_size = min_max
        metric, threshold = metric_threshold
        
        # Handle cooldown if present
        cooldown = 300  # Default 5 minutes
        if ',' in threshold:
            threshold, cooldown = threshold.split(',')
        
        return {
            "min_size": int(min_size),
            "max_size": int(max_size),
            "metric": metric,
            "threshold": float(threshold),
            "cooldown": int(cooldown)
        }
    except Exception as e:
        raise typer.BadParameter(f"Invalid autoscale format: {str(e)}")

def _get_logical_id(spec: Dict, stack: str) -> str:
    """Helper to determine the logical ID (name) of the resource/stack."""
    name = spec.get("name", "unnamed")
    # If stack is provided and not default, append it to avoid collisions
    if stack and stack != "dev":
        return f"{name}-{stack}"
    return name


# -------------------------------------------------------------
# Dynamic Command Fallback System
# -------------------------------------------------------------
class CloudbrewGroup(TyperGroup):
    def get_command(self, ctx, cmd_name: str):
        # check if static command exists first
        cmd = super().get_command(ctx, cmd_name)
        if cmd:
            return cmd

        # dynamic command fallback
        def dynamic_command(_args):
            raw_args = list(_args or [])
            name = "unnamed"
            params: Dict[str, Any] = {}

            # first non-flag token is the logical name (if present)
            idx = 0
            while idx < len(raw_args):
                if not raw_args[idx].startswith("--"):
                    name = raw_args[idx]
                    idx += 1
                    break
                idx += 1

            apply_flag=False
            if "-apply" in raw_args:
                raw_args.remove("-apply")
                apply_flag = True
            elif "--apply" in raw_args:
                raw_args.remove("--apply")
                apply_flag = True

            # parse --key value or boolean flags
            i = 0
            while i < len(raw_args):
                tok = raw_args[i]
                if tok.startswith("--"):
                    key = tok.lstrip("-")
                    # treat `--flag value` or `--flag` (boolean)
                    if i + 1 < len(raw_args) and not raw_args[i + 1].startswith("--"):
                        params[key] = raw_args[i + 1]
                        i += 2
                    else:
                        params[key] = True
                        i += 1
                else:
                    i += 1

            # control flags
            yes = bool(params.pop("yes", False) or params.pop("y", False) or apply_flag)
            should_apply = bool(apply_flag or yes)
            async_apply = bool(params.pop("async", False))
            provider_hint = params.pop("provider", "auto")

            # prepare resolver
            rr = ResourceResolver()

            resolved_meta = rr.canonicalize_identity(
                resource=cmd_name,
                provider_hint=provider_hint,
                logical_name=name,
            )
            if not isinstance(resolved_meta, dict):
                resolved_meta = {}
            resolved_provider = resolved_meta.get("_provider") or "opentofu"
            resolved_name = resolved_meta.get("_resolved") or cmd_name

            # Build a resolved dict that will be included in every output
            resolved_block = {
                "_provider": resolved_provider,
                "_resolved": resolved_name,
                "_identity": resolved_meta.get("_identity"),
            }
            if isinstance(resolved_meta, dict):
                # merge meta but keep core keys
                ignored_keys = {"block", "version", "description_kind"}
                for k, v in resolved_meta.items():
                    if k not in resolved_block and k not in ignored_keys:
                        resolved_block[k] = v

            # If not resolved, return helpful diagnostic JSON
            if not resolved_meta or not resolved_meta.get("_resolved"):
                out = {
                    "mode": "dynamic-fallback",
                    "resource": cmd_name,
                    "name": name,
                    "params": params,
                    "resolved": resolved_block,
                    "error": f"could not resolve resource '{cmd_name}'",
                    "failure": resolved_meta,
                }
                typer.echo(json.dumps(out, indent=2))
                return

            # --- AUTHENTICATION CHECK ---
            if resolved_provider != "noop":
                ensure_authenticated_for_resource(resolved_provider, cmd_name)

            pipeline_request = {
                "name": name,
                "resource_type": resolved_name or cmd_name,
                "provider": resolved_provider,
                "attributes": params,
                "plan_only": not should_apply,
                "non_interactive": yes,
            }

            try:
                result = provisioning_pipeline.execute(pipeline_request)
            except Exception as e:
                typer.echo(json.dumps({
                    "mode": "create-pipeline",
                    "resource": cmd_name,
                    "name": name,
                    "resolved": resolved_block,
                    "error": str(e),
                }, indent=2))
                return

            typer.echo(json.dumps({
                "mode": "create-pipeline",
                "resource": cmd_name,
                "name": name,
                "resolved": resolved_block,
                "result": result,
            }, indent=2))
            return

        # return click.Command accepting varargs and ignoring unknown options
        return click.Command(
            name=cmd_name,
            callback=dynamic_command,
            params=[click.Argument(["_args"], nargs=-1)],
            context_settings={"ignore_unknown_options": True},
            add_help_option=False,
        )


# -------------------------------------------------------------
# App Initialization
# -------------------------------------------------------------
app = typer.Typer(cls=CloudbrewGroup)
offload_app = typer.Typer()
pool_app = typer.Typer()  # <--- NEW: Pool Management Group

app.add_typer(offload_app, name="offload")
app.add_typer(pool_app, name="pool")


# Mount init + configure apps if available
try:
    from LCF import cli_init
except Exception:
    cli_init = None

try:
    from LCF import cli_configure
except Exception:
    cli_configure = None

if cli_init:
    try:
        app.command(name="init")(cli_init.init)
    except Exception:
        app.add_typer(cli_init.app, name="init")

if cli_configure:
    try:
        app.command(name="configure")(cli_configure.init)
    except Exception:
        try:
            app.command(name="configure")(cli_configure.configure)
        except Exception:
            app.add_typer(cli_configure.app, name="configure")


# -------------------------------------------------------------
#  Policy Helper
# -------------------------------------------------------------
def check_policy_or_die(spec: dict, skip: bool = False):
    """
    Runs policy engine, prints violations, and exits on ERROR severity.
    """
    if skip:
        return

    engine = PolicyEngine()  # loads policies.json or built-in rules
    violations = engine.check(spec)

    if violations:
        typer.secho(" POLICY VIOLATION DETECTED", fg=typer.colors.RED, bold=True)

        for v in violations:
            typer.echo(f"  [{v.severity}] {v.rule_id} on {v.resource_name}: {v.message}")

        # Block apply if any ERROR-level violation occurs
        if any(v.severity == "ERROR" for v in violations):
            raise typer.Exit(code=1)


# -------------------------------------------------------------
#  Pool Management Commands (NEW)
# -------------------------------------------------------------
@pool_app.command("run-worker")
def pool_worker(
    interval: int = typer.Option(60, help="Reconciliation interval in seconds"),
):
    """
    Starts the Warm Pool Manager background worker.
    Keeps Hot/Warm tiers filled based on targets.
    """
    wm = WarmPoolManager()
    typer.secho(f"Warm Pool Worker started. Interval: {interval}s", fg=typer.colors.GREEN)
    
    try:
        while True:
            try:
                wm.reconcile()
            except Exception as e:
                typer.secho(f"Error in reconciliation loop: {e}", fg=typer.colors.RED)
            
            time.sleep(interval)
    except KeyboardInterrupt:
        typer.secho("Worker stopped.", fg=typer.colors.YELLOW)

@pool_app.command("status")
def pool_status():
    """
    Shows the current state of the Hot/Warm pools.
    """
    # Simple query to the pool DB
    db_path = WarmPoolManager.DB_PATH
    import sqlite3
    
    if not os.path.exists(db_path):
        typer.echo("No pool database found.")
        return

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT tier, status, count(*) as cnt FROM resource_pool GROUP BY tier, status")
        rows = cur.fetchall()
        
    typer.secho("Pool Status:", bold=True)
    if not rows:
        typer.echo("  (Empty)")
    for r in rows:
        typer.echo(f"  {r['tier'].upper():<5} | {r['status']:<10} : {r['cnt']}")

stack_app = typer.Typer()
app.add_typer(stack_app, name="stack")

@stack_app.command("modify")
def stack_modify(
    stack_name: str = typer.Argument(..., help="Target stack name"),
):
    """
    Interactive stack management: Create, Destroy, or Status.
    """
    typer.secho(f"Modifying Stack: {stack_name}", bold=True, fg=typer.colors.BLUE)
    action = typer.prompt("Select action (Create/Destroy/Status)").strip().lower()

    sm = StackManager()
    st = store.SQLiteStore(DEFAULT_DB)

    if action == "create":
        # Scaffold logic
        path = sm.scaffold(stack_name)
        typer.secho(f"Blueprint scaffolded at: {path}", fg=typer.colors.GREEN)
        typer.echo("Edit this file and run 'cloudbrew stack deploy' to apply.")

    elif action == "destroy":
        # Find all resources with tag match
        # Note: This relies on your adapter saving tags in the spec JSON in DB
        typer.secho(f"Locating resources for stack '{stack_name}'...", fg=typer.colors.YELLOW)
        
        all_inst = st.list_instances()
        targets = []
        
        for inst in all_inst:
            spec = inst.get("spec", {})
            tags = spec.get("tags", {})
            # Match strict tag "stack" or convention in name
            if tags.get("stack") == stack_name or stack_name in inst["logical_id"]:
                targets.append(inst)

        if not targets:
            typer.echo("No resources found matching this stack.")
            return

        typer.echo(f"Found {len(targets)} resources.")
        if click.confirm("Destroy them?"):
            ta = OpenTofuAdapter() # Or use OffloadManager here for async
            for t in targets:
                adapter_id = t.get("adapter_id")
                if adapter_id:
                    typer.echo(f"Destroying {t['logical_id']}...")
                    ta.destroy_instance(adapter_id)
            typer.secho("Stack destruction complete.", fg=typer.colors.GREEN)

    elif action == "status":
        # Real-time state check
        all_inst = st.list_instances()
        found = False
        typer.echo(f"{'RESOURCE':<30} | {'STATE':<15} | {'PROVIDER':<10}")
        typer.echo("-" * 60)
        
        for inst in all_inst:
            spec = inst.get("spec", {})
            tags = spec.get("tags", {})
            
            if tags.get("stack") == stack_name or stack_name in inst["logical_id"]:
                found = True
                state = inst.get("state", "unknown").upper()
                color = typer.colors.GREEN if state == "RUNNING" else typer.colors.YELLOW
                typer.secho(f"{inst['logical_id']:<30} | {state:<15} | {inst['adapter']:<10}", fg=color)
        
        if not found:
            typer.echo("Stack not found or empty.")

    else:
        typer.secho("Invalid option.", fg=typer.colors.RED)

# -------------------------------------------------------------
#  Stack Commands (Blueprint-based Multi-resource Deployments)
# -------------------------------------------------------------
@stack_app.command("deploy")
def stack_deploy(
    blueprint: str = typer.Argument(..., help="Name of the stack blueprint (e.g., lamp)"),
    name: str = typer.Argument(..., help="Name for this stack instance"),
    region: str = typer.Option("us-east-1"),
    env: str = typer.Option("dev", help="Environment context (dev, prod)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only; do not create resources"),
):
    """
    Deploy a multi-resource stack using stack blueprints.
    """
    sm = StackManager()

    # Validate blueprint availability
    if blueprint not in sm.list_stacks() and not os.path.exists(blueprint):
        typer.secho(f"Blueprint '{blueprint}' not found.", fg=typer.colors.RED)
        typer.echo("Available stacks:")
        for k, desc in sm.list_stacks().items():
            typer.echo(f" - {k}: {desc}")
        raise typer.Exit(1)

    typer.secho(f" Initializing Stack '{name}' (Blueprint: {blueprint})...", fg=typer.colors.BLUE)
    params = {"region": region, "env": env}

    result = sm.deploy_stack(blueprint, name, params, dry_run=dry_run)

    if result.success:
        typer.secho(f" Stack completed in {result.elapsed_time:.2f}s", fg=typer.colors.GREEN)
        typer.echo(f"Resources processed: {result.resources_created}")
    else:
        typer.secho(f" Stack completed with errors ({result.elapsed_time:.2f}s)", fg=typer.colors.YELLOW)
        for err in result.errors:
            typer.echo(f"  - {err}")
        if not result.resources_created:
            raise typer.Exit(1)


@app.command("stacks")
def list_stacks():
    """
    List supported stack blueprints.
    """
    sm = StackManager()
    typer.secho("Available Blueprints:", bold=True)
    for name, desc in sm.list_stacks().items():
        typer.echo(f"{name:<15} : {desc}")


# -------------------------------------------------------------
#  Drift Detection Command
# -------------------------------------------------------------
@app.command("drift")
def check_drift_cmd(
    name: str = typer.Argument(..., help="Logical ID of the resource to check"),
):
    """
    Check for OpenTofu drift (manual cloud modifications).
    """
    # Add authentication check for drift operations
    ensure_authenticated_for_resource("opentofu", "resource")
    
    ta = OpenTofuAdapter()

    typer.echo(f" Checking drift for {name}...")
    res = ta.check_drift(name)

    drifted = res.get("drifted")

    if drifted is None:
        typer.secho(f" Unable to determine drift state: {res.get('msg')}", fg=typer.colors.YELLOW)

    elif drifted:
        typer.secho(" DRIFT DETECTED", fg=typer.colors.RED, bold=True)
        typer.echo(f"Summary: {res.get('summary')}")
        typer.echo("Details (trimmed):")
        typer.echo(res.get("details"))
        raise typer.Exit(code=2)

    else:
        typer.secho(" No drift. Infrastructure is in sync.", fg=typer.colors.GREEN)


# -------------------------------------------------------------
#  Updated create-vm (Policy + Tags + Confirmations + Intelligent Router)
# -------------------------------------------------------------
@app.command("create-vm")
def create_vm(
    name: str,
    image: str = typer.Option("ubuntu-22.04"),
    size: str = typer.Option("small"),
    region: str = typer.Option("us-east-1"),
    count: int = typer.Option(1),
    provider: str = typer.Option("auto"), 
    db_path: Optional[str] = typer.Option(DEFAULT_DB),
    yes: bool = typer.Option(False, "--yes", "-y"),
    async_apply: bool = typer.Option(False, "--async"),
    offload_db: str = typer.Option(DEFAULT_OFFLOAD_DB),

    # NEW flags
    tags: str = typer.Option("{}", help="JSON tags"),
    skip_policy: bool = typer.Option(False, "--skip-policy", help="Bypass governance policy checks"),
    spec: Optional[str] = typer.Option(None, help="Optional JSON spec file instead of flags"),
    router_mode: bool = typer.Option(True, "--smart/--standard", help="Use Intelligent Router"),
):
    """
    Create a VM with policy enforcement, intelligent routing (cache), tags, and plan preview.
    """

    # --------------------------------------------
    # Build spec
    # --------------------------------------------
    if spec:
        s = _load_spec(spec)
    else:
        try:
            tag_dict = json.loads(tags)
        except json.JSONDecodeError:
            raise typer.BadParameter("Invalid JSON passed to --tags")

        s = {
            "name": name,
            "type": "vm",
            "image": image,
            "size": size,
            "region": region,
            "count": count,
            "tags": tag_dict,
            "provider": provider
        }
    
    # --------------------------------------------
    # RESOLVE & MERGE DEFAULTS 
    # --------------------------------------------
    rr = ResourceResolver()
    res = rr.canonicalize_identity(
        resource=s["type"],
        provider_hint=s.get("provider", "auto"),
        logical_name=s.get("name"),
    )

    if isinstance(res, dict) and res.get("mode") == "provider_native_type_unmapped":
        typer.echo(json.dumps({"error": res.get("message"), "details": res}, indent=2))
        raise typer.Exit(code=2)

    if res and "_resolved" in res:
        # Update type (e.g. 'vm' -> 'aws_instance')
        s["type"] = res["_resolved"]
        
        # Update provider if implied by mapping
        if res.get("_provider") and res["_provider"] != "auto":
            s["provider"] = res["_provider"]
            
        # Merge Defaults from aws.json (including tags)
        defaults = res.get("_defaults", {})
        for k, v in defaults.items():
            # Special handling for tags: merge instead of overwrite
            if k == "tags":
                current_tags = s.get("tags", {})
                default_tags = v
                
                # If CLI tags were empty, take defaults
                if not current_tags:
                    import copy
                    s["tags"] = copy.deepcopy(default_tags)
                # If both exist, merge them
                elif isinstance(current_tags, dict) and isinstance(default_tags, dict):
                    import copy
                    merged = copy.deepcopy(default_tags)
                    merged.update(current_tags)
                    s["tags"] = merged
            
            # Standard merge for other fields (e.g. ami, instance_type)
            elif k not in s or s[k] is None:
                import copy
                s[k] = copy.deepcopy(v)

    # --------------------------------------------
    # Policy Check
    # --------------------------------------------
    check_policy_or_die(s, skip=skip_policy)

    # --------------------------------------------
    # AUTHENTICATION CHECK
    # --------------------------------------------
    # Check if user is authenticated for this provider before proceeding
    provider = s.get("provider") or get_default_provider() or "noop"
    if provider != "noop":
        ensure_authenticated_for_resource(provider, "vm")

    # --------------------------------------------
    # INTELLIGENT ROUTING MODE (Default)
    # --------------------------------------------
    if router_mode and not async_apply:
        typer.secho(" Using Intelligent Router...", fg=typer.colors.MAGENTA)
        router = IntelligentRouter()
        
        # This handles L1/L2 Cache hit OR falls back to cold build
        res = router.provision(name, s)
        
        # Output handling
        source = res.get("source", "UNKNOWN")
        latency = res.get("latency", "N/A")
        
        if source == "L1_CACHE_HIT":
            typer.secho(f" HOT CACHE HIT! ({latency})", fg=typer.colors.bright_green, bold=True)
            typer.echo(f"ID: {res.get('id')}")
            typer.echo(f"Connection: {res.get('details')}")
        elif source == "L2_WARM_HIT":
             typer.secho(f" WARM CACHE HIT! ({latency})", fg=typer.colors.green)
        else:
            typer.echo(f" ce ({latency})")
            
        typer.echo(json.dumps(res, indent=2))
        return

    # --------------------------------------------
    # STANDARD MODE / Fallback / Async Logic
    # --------------------------------------------
    typer.echo("Using Standard Provisioning Workflow...")

    # Auto provider selection
    chosen_provider = provider
    if provider == "auto":
        if os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"):
            chosen_provider = "aws"
        else:
            chosen_provider = "opentofu"

    autoscale_cfg = {
        "min": s.get("count", count),
        "max": s.get("count", count),
        "policy": [],
        "cooldown": 60,
    }

    mgr = AutoscalerManager(db_path=db_path, provider=chosen_provider)

    # Async execution
    if async_apply:
        res = mgr.run_once(name, s, autoscale_cfg,
            observed_metrics={"cpu": 0},
            plan_only=True
        )

        plan_id = None
        for a in res.get("actions", []):
            plan_id = a.get("res", {}).get("plan_id") or plan_id

        off = OffloadManager(offload_db)
        tid = off.enqueue(adapter=chosen_provider, task_type="apply_plan",
                          payload={"plan_path": plan_id})
        typer.echo(json.dumps({
            "enqueued_task_id": tid,
            "plan_id": plan_id
        }, indent=2))
        return

    # Direct apply if confirmed
    if yes:
        res = mgr.run_once(name, s, autoscale_cfg,
            observed_metrics={"cpu": 0},
            plan_only=False
        )
        typer.echo(json.dumps(res, indent=2))
        return

    # Plan-only (ask user for confirmation)
    res = mgr.run_once(name, s, autoscale_cfg,
        observed_metrics={"cpu": 0},
        plan_only=True
    )

    typer.echo(json.dumps(res, indent=2))

    # Ask for confirmation before apply
    if not click.confirm("Apply this plan?"):
        raise typer.Abort()

    # Apply after confirmation
    res = mgr.run_once(name, s, autoscale_cfg,
                       observed_metrics={"cpu": 0},
                       plan_only=False)
    typer.echo(json.dumps(res, indent=2))


# -------------------------------------------------------------
#  Intelligent Create - NEW FEATURE
# -------------------------------------------------------------
@app.command("create")
def create_resource(
    resource_type: str = typer.Argument(..., help="Resource type (e.g., aws_instance)"),
    name: str = typer.Argument(..., help="Resource name"),
    autoscale: Optional[str] = typer.Option(None, "--autoscale", help="Autoscaling configuration (format: min:max@metric:threshold,cooldown)"),
    spec: Optional[str] = typer.Option(None, "--spec", help="Path to JSON/YAML/CBDSL spec file"),
    provider: Optional[str] = typer.Option(None, "--provider", help="Cloud provider (aws, google, azure)"),
    bucket: Optional[str] = typer.Option(None, "--bucket", help="S3 bucket name"),
    acl: Optional[str] = typer.Option(None, "--acl", help="S3 bucket ACL (private, public-read, etc.)"),
    versioning: Optional[bool] = typer.Option(False, "--versioning", help="Enable versioning"),
    apply: bool = typer.Option(False, "--apply", help="Apply the configuration (default: plan only)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Non-interactive mode"),
    async_apply: bool = typer.Option(False, "--async", help="Offload task to background worker"),
):
    """Create cloud resources through the canonical provisioning pipeline."""

    attributes: Dict[str, Any] = {}
    if spec:
        attributes.update(_load_spec(spec) or {})

    if autoscale:
        attributes["autoscale"] = parse_autoscale_config(autoscale)
    if provider:
        attributes["provider"] = provider
    if bucket:
        attributes["bucket"] = bucket
    if acl:
        attributes["acl"] = acl
    if versioning:
        attributes["versioning"] = {"enabled": bool(versioning)}

    resolved_provider = attributes.get("provider") or provider or get_default_provider() or "opentofu"
    if resolved_provider != "noop":
        ensure_authenticated_for_resource(resolved_provider, resource_type)

    should_apply = bool(apply and yes)

    if async_apply:
        off = OffloadManager(DEFAULT_OFFLOAD_DB)
        tid = off.enqueue(
            adapter=resolved_provider,
            task_type="pipeline_create",
            payload={
                "name": name,
                "resource_type": resource_type,
                "provider": resolved_provider,
                "attributes": attributes,
                "plan_only": not should_apply,
                "non_interactive": yes,
            },
        )
        typer.echo(json.dumps({"enqueued_task_id": tid}, indent=2))
        return

    result = provisioning_pipeline.execute(
        {
            "name": name,
            "resource_type": resource_type,
            "provider": resolved_provider,
            "attributes": attributes,
            "plan_only": not should_apply,
            "non_interactive": yes,
        }
    )
    typer.echo(json.dumps(result, indent=2))
    if not result.get("success"):
        raise typer.Exit(1)


# -------------------------------------------------------------
#  destroy-vm (OpenTofu + Pulumi + Offload)
# -------------------------------------------------------------
@app.command("destroy-vm")
def destroy_vm(
    name: str,
    provider: str = typer.Option("opentofu"),
    db_path: Optional[str] = typer.Option(DEFAULT_DB),
    offload: bool = typer.Option(False),
    offload_db: str = typer.Option(DEFAULT_OFFLOAD_DB),
):
    """
    Destroy a VM or resource managed by OpenTofu or Pulumi.
    """

    if offload:
        off = OffloadManager(offload_db)
        tid = (
            off.enqueue("pulumi", "destroy_stack", {"stack": name})
            if provider == "pulumi"
            else off.enqueue("opentofu", "destroy", {"adapter_id": f"opentofu-{name}"})
        )
        typer.echo(json.dumps({"enqueued_task_id": tid}))
        return

    if provider == "pulumi":
        for line in pulumi_adapter.destroy(name):
            typer.echo(line)
    else:
        ta = OpenTofuAdapter(db_path)
        st = store.SQLiteStore(db_path)
        inst = st.get_instance(name)
        target_adapter_id = None

        if inst:
            target_adapter_id = inst.get("adapter_id") or f"opentofu-{inst.get('logical_id', name)}"
        else:
            for row in st.list_instances(adapter="opentofu"):
                spec = row.get("spec", {}) or {}
                if spec.get("bucket") == name or spec.get("name") == name:
                    target_adapter_id = row.get("adapter_id") or f"opentofu-{row.get('logical_id', name)}"
                    break

        if not target_adapter_id:
            target_adapter_id = f"opentofu-{name}"

        destroy_res = ta.destroy_instance(target_adapter_id)
        typer.echo(json.dumps({"destroyed": bool(destroy_res.get("success")), "name": name, "result": destroy_res}))


# Destroy alias
@app.command("destroy")
def destroy_alias(
    name: str,
    provider: str = typer.Option("opentofu"),
    db_path: Optional[str] = typer.Option(DEFAULT_DB),
    offload: bool = typer.Option(False),
    offload_db: str = typer.Option(DEFAULT_OFFLOAD_DB),
):
    """
    Alias for destroy-vm. Maintained for backward compatibility.
    """
    # Add authentication check for destroy operations
    if provider != "noop":
        ensure_authenticated_for_resource(provider, "resource")
    
    return destroy_vm(name, provider=provider, db_path=db_path,
                      offload=offload, offload_db=offload_db)


# -------------------------------------------------------------
# 1. PULUMI COMMANDS (Updated with Flags & Parity)
# -------------------------------------------------------------
@app.command("pulumi-plan")
def cli_pulumi_plan(
    stack: str = typer.Option("dev", "--stack"),
    spec_file: str = typer.Option("spec.json", "--spec-file", "--spec", help="Path to JSON or YAML spec"),
):
    """
    Run Pulumi plan/preview.
    """
    spec = _load_spec(spec_file)
    typer.secho(f"Planning Pulumi stack '{stack}'...", fg=typer.colors.BLUE)
    for line in pulumi_adapter.plan(spec, stack):
        typer.echo(line)


@app.command("pulumi-apply")
def cli_pulumi_apply(
    stack: str = typer.Option("dev", "--stack"),
    spec_file: str = typer.Option("spec.json", "--spec-file", "--spec", help="Path to JSON or YAML spec"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    offload: bool = typer.Option(False, "--offload"),
):
    """
    Run Pulumi apply/up.
    """
    try:
        spec = _load_spec(spec_file)
    except typer.BadParameter:
        typer.secho(f"Warning: '{spec_file}' not found. Using empty spec for apply.", fg=typer.colors.YELLOW)
        spec = {"name": stack}

    if offload:
        off = OffloadManager(DEFAULT_OFFLOAD_DB)
        tid = off.enqueue("pulumi", "apply_spec", {"spec": spec, "stack": stack})
        typer.echo(json.dumps({"enqueued_task_id": tid}))
        return

    typer.secho(f"Applying Pulumi stack '{stack}'...", fg=typer.colors.BLUE)
    for line in pulumi_adapter.apply(spec, stack):
        typer.echo(line)


@app.command("pulumi-destroy")
def cli_pulumi_destroy(
    stack: str = typer.Option("dev", "--stack"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    offload: bool = typer.Option(False, "--offload"),
):
    """
    Run Pulumi destroy.
    """
    if offload:
        off = OffloadManager(DEFAULT_OFFLOAD_DB)
        tid = off.enqueue("pulumi", "destroy_stack", {"stack": stack})
        typer.echo(json.dumps({"enqueued_task_id": tid}))
        return

    if not yes:
        typer.confirm(f"Are you sure you want to DESTROY Pulumi stack '{stack}'?", abort=True)

    typer.secho(f"Destroying Pulumi stack '{stack}'...", fg=typer.colors.RED)
    for line in pulumi_adapter.destroy(stack):
        typer.echo(line)


# -------------------------------------------------------------
# 2. OPENTOFU COMMANDS (Parity Added)
# -------------------------------------------------------------
@app.command("tofu-plan")
def cli_tofu_plan(
    stack: str = typer.Option("dev", "--stack"),
    spec_file: str = typer.Option("spec.json", "--spec-file", "--spec", help="Path to JSON or YAML spec"),
):
    """
    Run OpenTofu plan.
    Example: cloudbrew tofu-plan --spec examples/vm.json
    """
    spec = _load_spec(spec_file)
    logical_id = _get_logical_id(spec, stack)
    
    typer.secho(f"Planning OpenTofu resource '{logical_id}'...", fg=typer.colors.MAGENTA)
    
    ta = OpenTofuAdapter()
    res = ta.create_instance(logical_id, spec, plan_only=True)
    
    if isinstance(res, dict) and "diff" in res:
        typer.echo(res["diff"])
    else:
        typer.echo(json.dumps(res, indent=2))


@app.command("tofu-apply")
def cli_tofu_apply(
    stack: str = typer.Option("dev", "--stack"),
    spec_file: str = typer.Option("spec.json", "--spec-file", "--spec", help="Path to JSON or YAML spec"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    offload: bool = typer.Option(False, "--offload"),
):
    """
    Run OpenTofu apply.
    Example: cloudbrew tofu-apply --spec examples/vm.json --yes
    """
    spec = _load_spec(spec_file)
    logical_id = _get_logical_id(spec, stack)

    if offload:
        off = OffloadManager(DEFAULT_OFFLOAD_DB)
        tid = off.enqueue("opentofu", "create_instance", {"name": logical_id, "spec": spec})
        typer.echo(json.dumps({"enqueued_task_id": tid}))
        return

    if not yes:
        typer.confirm(f"Apply OpenTofu configuration for '{logical_id}'?", abort=True)

    typer.secho(f"Applying OpenTofu resource '{logical_id}'...", fg=typer.colors.MAGENTA)
    
    ta = OpenTofuAdapter()
    res = ta.create_instance(logical_id, spec, plan_only=False)
    
    if res.get("success"):
        typer.secho(" Success!", fg=typer.colors.GREEN)
        if "output" in res:
            typer.echo(res["output"])
    else:
        typer.secho(" Failed:", fg=typer.colors.RED)
        typer.echo(json.dumps(res, indent=2))
        raise typer.Exit(code=1)


@app.command("tofu-destroy")
def cli_tofu_destroy(
    stack: str = typer.Option("dev", "--stack"),
    spec_file: Optional[str] = typer.Option(None, "--spec-file", "--spec"),
    name: Optional[str] = typer.Option(None, "--name"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    offload: bool = typer.Option(False, "--offload"),
):
    """
    Run OpenTofu destroy.
    Example: cloudbrew tofu-destroy --spec examples/vm.json --yes
    """
    logical_id = name
    if not logical_id and spec_file:
        try:
            spec = _load_spec(spec_file)
            logical_id = _get_logical_id(spec, stack)
        except Exception:
            pass
    
    if not logical_id:
        if stack != "dev":
            logical_id = stack
        else:
             raise typer.BadParameter("Could not determine resource name. Provide --spec or --name.")

    adapter_id = f"opentofu-{logical_id}"

    if offload:
        off = OffloadManager(DEFAULT_OFFLOAD_DB)
        tid = off.enqueue("opentofu", "destroy", {"adapter_id": adapter_id})
        typer.echo(json.dumps({"enqueued_task_id": tid}))
        return

    if not yes:
        typer.confirm(f"Are you sure you want to DESTROY '{adapter_id}'?", abort=True)

    typer.secho(f"Destroying OpenTofu resource '{adapter_id}'...", fg=typer.colors.RED)
    
    ta = OpenTofuAdapter()
    try:
        destroy_res = ta.destroy_instance(adapter_id)
        if destroy_res.get("success"):
            typer.secho(" Destroy complete.", fg=typer.colors.GREEN)
        else:
            typer.secho(" Destroy returned failure.", fg=typer.colors.RED)
            typer.echo(json.dumps(destroy_res, indent=2))
    except Exception as e:
        typer.secho(f" Destroy failed: {e}", fg=typer.colors.RED)


# -------------------------------------------------------------
# 3. AUTOSCALER COMMANDS
# -------------------------------------------------------------
@app.command("autoscale")
def autoscale_cmd(
    target: str = typer.Option(..., "--target", help="Target resource name/ID"),
    policy: str = typer.Option(..., "--policy", help="Policy string (e.g., 'cpu>80%:scale+2')"),
    db_path: str = typer.Option(DEFAULT_DB, help="Path to DB"),
):
    """
    Attach or update autoscaling policies for a target.
    """
    st = store.SQLiteStore(db_path)
    instance = st.get_instance(target)
    
    if not instance:
        typer.secho(f"Error: Target '{target}' not found in CloudBrew DB.", fg=typer.colors.RED)
        raise typer.Exit(1)
    
    current_spec = instance.get("spec", {})
    typer.secho(f"Updating autoscaling policy for '{target}'...", fg=typer.colors.GREEN)
    typer.echo(f"  Old Policy: {current_spec.get('autoscale', 'None')}")
    typer.echo(f"  New Policy: {policy}")
    
    current_spec["autoscale"] = policy
    instance["spec"] = current_spec
    st.upsert_instance(instance)
    
    typer.secho(" Policy updated successfully.", fg=typer.colors.GREEN)


# -------------------------------------------------------------
# 4. GENERIC PLAN / APPLY-PLAN COMMANDS (Preserved)
# -------------------------------------------------------------
@app.command("plan")
def plan_cmd(
    provider: str = typer.Option("opentofu"),
    spec_file: Optional[str] = typer.Option(None, "--spec-file", "-f", "--spec"),
    spec_json: Optional[str] = typer.Option(None, "--spec-json"),
    db_path: Optional[str] = typer.Option(DEFAULT_DB),
):
    """
    Produce a plan for a given provider. Prints JSON summary.
    """

    if spec_file and spec_json:
        raise typer.BadParameter("Use only one of --spec-file or --spec-json")

    if spec_file:
        s = _load_spec(spec_file)
    elif spec_json:
        try:
            s = json.loads(spec_json)
        except Exception as e:
            raise typer.BadParameter(f"Invalid --spec-json: {e}")
    else:
        raise typer.BadParameter("Either --spec-file or --spec-json must be provided")

    if provider in ("opentofu", "tofu"):
        ta = OpenTofuAdapter(db_path=db_path)
        res = ta.create_instance(s.get("name", "plan-object"), s, plan_only=True)
        typer.echo(json.dumps(res, indent=2))

    elif provider == "pulumi":
        gen = pulumi_adapter.plan(s, "dev")
        try:
            lines = []
            for ln in gen:
                lines.append(ln)
            typer.echo(json.dumps({"plan_output": lines}, indent=2))
        except TypeError:
            typer.echo(json.dumps(gen, indent=2))

    else:
        raise typer.BadParameter(f"Unsupported provider: {provider}")


@app.command("apply-plan")
def apply_plan_cmd(
    provider: str = typer.Option("opentofu"),
    plan_id: str = typer.Option(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
    async_apply: bool = typer.Option(False, "--async"),
    offload_db: str = typer.Option(DEFAULT_OFFLOAD_DB),
):
    """
    Apply a previously generated plan (OpenTofu or Pulumi).
    """

    if async_apply:
        off = OffloadManager(offload_db)
        tid = off.enqueue(provider, "apply_plan", {"plan_id": plan_id})
        typer.echo(json.dumps({"enqueued_task_id": tid, "plan_id": plan_id}, indent=2))
        return

    if provider in ("opentofu", "tofu"):
        ta = OpenTofuAdapter()
        res = ta.apply_plan(plan_id)
        typer.echo(json.dumps(res, indent=2))

    elif provider == "pulumi":
        gen = pulumi_adapter.apply(plan_id, "dev")
        try:
            lines = []
            for ln in gen:
                lines.append(ln)
            typer.echo(json.dumps({"apply_output": lines}, indent=2))
        except TypeError:
            typer.echo(json.dumps(gen, indent=2))

    else:
        raise typer.BadParameter(f"Unsupported provider: {provider}")


# -------------------------------------------------------------
# Offload Commands (Worker + Enqueue)
# -------------------------------------------------------------
@offload_app.command("enqueue")
def offload_enqueue(
    adapter: str = typer.Option("opentofu"),
    task_type: str = typer.Option(...),
    payload: str = typer.Option("{}", help="JSON payload"),
):
    """
    Enqueue an async task.
    """
    off = OffloadManager()
    p = json.loads(payload)
    tid = off.enqueue(adapter=adapter, task_type=task_type, payload=p)
    typer.echo(json.dumps({"task_id": tid}))


@offload_app.command("run-worker")
def offload_run_worker(
    db_path: str = typer.Option(DEFAULT_OFFLOAD_DB),
    poll_interval: int = typer.Option(5),
    concurrency: int = typer.Option(1),
):
    """
    Run the async task worker.
    """
    off = OffloadManager(db_path)
    typer.secho(f"Starting Offload Worker (DB: {db_path})...", fg=typer.colors.MAGENTA)
    try:
        off.run_worker(poll_interval=poll_interval, concurrency=concurrency)
    except KeyboardInterrupt:
        off.stop()


# -------------------------------------------------------------
# Status command (DB instance list)
# -------------------------------------------------------------
@app.command("status")
def status_cmd(
    db_path: Optional[str] = typer.Option(DEFAULT_DB, help="Path to SQLite DB (default cloudbrew.db)"),
):
    """
    Display all known instances from the local CloudBrew DB.
    """
    st = store.SQLiteStore(db_path)
    instances = st.list_instances()
    typer.echo(json.dumps({"instances": instances}, indent=2))


# -------------------------------------------------------------
# CLI Entrypoint
# -------------------------------------------------------------
if __name__ == "__main__":
    app()
