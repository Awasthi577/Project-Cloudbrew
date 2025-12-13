# LCF/cli.py
"""
Cloudbrew CLI entrypoint.

Includes:
- Intelligent Routing (Hot/Warm/Cold Tiering)
- Dynamic commands (via CloudbrewGroup)
- Policy Engine integration
- Stack Manager integration
- Drift Detection (via OpenTofu)
- Updated VM Create workflow w/ tags + skip-policy
- Pulumi + OpenTofu support
- Autoscaler integration
- Offload worker support
"""

from __future__ import annotations

import json
import os
import sys
import click
import time
from pathlib import Path
from typing import Optional, List, Dict, Any

import typer
from typer.core import TyperGroup

# -------------------------------------------------------------
# Local Imports
# -------------------------------------------------------------
import typer
import click

from LCF.resource_resolver import ResourceResolver
from LCF.autoscaler import AutoscalerManager, parse_autoscale_string
# from LCF.offload.manager import OffloadManager  # Commented out due to missing module
from LCF.cloud_adapters import pulumi_adapter
from LCF.cloud_adapters.opentofu_adapter import OpenTofuAdapter
# from LCF.schema_provisioner import SchemaProvisioner, interactive_create_command
from LCF.schema_provisioner_enhanced import EnhancedSchemaProvisioner, enhanced_create_command

# Feature Imports
from LCF.policy_engine import PolicyEngine
from LCF.stack_manager import StackManager
from LCF.intelligent_router import IntelligentRouter
from LCF.pool_manager import WarmPoolManager

# Create the app early so commands can be registered
app = typer.Typer()
offload_app = typer.Typer()
pool_app = typer.Typer()

# -------------------------------------------------------------
# Constants
# -------------------------------------------------------------
DEFAULT_DB = "cloudbrew.db"
DEFAULT_OFFLOAD_DB = "cloudbrew_offload.db"


# -------------------------------------------------------------
# Schema Creation Command (defined early to ensure registration)
# -------------------------------------------------------------
@app.command("schema-create")
def schema_create_command(
    resource_type: str = typer.Argument(..., help="Resource type (e.g., aws_instance)"),
    name: str = typer.Argument(..., help="Name for the resource"),
    provider: str = typer.Option("auto", help="Provider hint (aws, gcp, azure, etc.)"),
    ami: str = typer.Option("ami-0a63f6f9f6f9abcde", help="AMI ID for AWS instances"),
    instance_type: str = typer.Option("t3.micro", help="Instance type for AWS instances"),
    region: str = typer.Option("us-east-1", help="AWS region"),
    auto_apply: bool = typer.Option(False, "--auto-apply", help="Automatically apply the plan without confirmation"),
):
    """
    Create a resource using schema-driven provisioning.
    
    This command resolves the resource type to the appropriate provider resource
    and creates it using OpenTofu.
    """
    # Resolve the resource type and provider
    rr = ResourceResolver()
    resolution = rr.resolve(resource=resource_type, provider=provider)
    
    if "_resolved" not in resolution or "_provider" not in resolution:
        typer.echo(f"Error: Could not resolve resource '{resource_type}'")
        typer.echo(f"Available providers: {resolution.get('tried_providers', [])}")
        typer.echo(f"Top candidates: {resolution.get('top_candidates', [])}")
        return {"success": False, "error": "Resource resolution failed"}
    
    resolved_provider = resolution["_provider"]
    resolved_resource_type = resolution["_resolved"]
    
    typer.echo(f"Resolved: {resource_type} -> {resolved_provider}:{resolved_resource_type}")
    
    # For AWS instances, use the provided parameters
    if resolved_provider == "aws" and "instance" in resolved_resource_type.lower():
        spec = {
            "provider": resolved_provider,
            "type": resolved_resource_type,
            "name": name,
            "ami": ami,
            "instance_type": instance_type,
            "region": region,
            "tags": {
                "Name": name,
                "ManagedBy": "CloudBrew-SchemaCreate"
            }
        }
    else:
        # For other resource types, use a minimal spec
        spec = {
            "provider": resolved_provider,
            "type": resolved_resource_type,
            "name": name
        }
    
    # Show configuration summary
    typer.echo("\n=== Configuration Summary ===")
    typer.echo(f"Provider: {resolved_provider}")
    typer.echo(f"Resource Type: {resolved_resource_type}")
    typer.echo(f"Name: {name}")
    typer.echo("Configuration:")
    for key, value in spec.items():
        if key not in ["provider", "type", "name"]:
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    typer.echo(f"  {key}.{sub_key}: {sub_value}")
            else:
                typer.echo(f"  {key}: {value}")
    
    # Confirm before proceeding
    if not auto_apply:
        if not typer.confirm("\nProceed with creation?"):
            typer.echo("Creation cancelled by user.")
            return {"success": False, "error": "User cancelled"}
    
    # Create the resource using the OpenTofu adapter
    ta = OpenTofuAdapter()
    
    try:
        # First create a plan
        typer.echo("\n[INFO] Creating plan...")
        plan_result = ta.create_instance(name, spec, plan_only=True)
        typer.echo("[SUCCESS] Plan created successfully!")
        
        # Show plan information
        if isinstance(plan_result, dict):
            typer.echo(f"Plan ID: {plan_result.get('plan_id', 'N/A')}")
            typer.echo(f"Status: {plan_result.get('status', 'unknown')}")
        
        # Apply the plan if auto_apply is True or user confirms
        apply_plan = auto_apply
        if not auto_apply:
            apply_plan = typer.confirm("\nApply this plan to create the resource?")
        
        if apply_plan:
            typer.echo("\n[INFO] Applying plan...")
            apply_result = ta.create_instance(name, spec, plan_only=False)
            typer.echo("[SUCCESS] Resource created successfully!")
            return {"success": True, "result": apply_result, "status": "applied"}
        else:
            typer.echo("\n[INFO] Plan created but not applied.")
            typer.echo("You can apply it later using: cloudbrew apply-plan <plan-id>")
            return {"success": True, "result": plan_result, "status": "plan_created"}
            
    except Exception as e:
        typer.echo(f"[ERROR] Error creating resource: {str(e)}")
        return {"success": False, "error": str(e)}


# -------------------------------------------------------------
# Dynamic Command Fallback System
# -------------------------------------------------------------
class CloudbrewGroup(TyperGroup):
    def get_command(self, ctx, cmd_name: str):
        # Special handling for schema-create command - force it to use static command
        if cmd_name == 'schema-create':
            cmd = super().get_command(ctx, cmd_name)
            if cmd:
                return cmd
            else:
                # Command not found, this shouldn't happen if it's properly registered
                raise typer.Exit(code=1)

        # Commands that should NEVER use dynamic fallback
        protected_commands = {
            'init', 'configure', 'stack', 'stacks', 'drift', 
            'create-vm', 'create', 'create-cluster', 'destroy-vm', 'destroy', 
            'pulumi-plan', 'pulumi-apply', 'pulumi-destroy', 'plan', 'apply-plan', 
            'status', 'offload', 'pool', 'tofu-plan', 'tofu-apply', 'tofu-destroy',
            'autoscale'
        }
        
        # If it's a protected command, don't use dynamic fallback
        if cmd_name in protected_commands:
            # Let Typer handle it normally (will show "No such command" if not found)
            return super().get_command(ctx, cmd_name)

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
            yes = bool(params.pop("yes", False) or params.pop("y", False))
            async_apply = bool(params.pop("async", False))
            provider_hint = params.pop("provider", "auto")

            # prepare resolver
            rr = ResourceResolver()

            def normalize_resolve_result(res) -> tuple[str, str, Dict[str, Any]]:

                if isinstance(res, dict):
                    provider = res.get("_provider") or provider_hint
                    resolved = res.get("_resolved") or res.get("resource") or res.get("name") or cmd_name
                    return str(provider), str(resolved), res
                if isinstance(res, str):
                    return provider_hint, res, {"_resolved": res, "_provider": provider_hint}
                # fallback
                return provider_hint, cmd_name, {"_resolved": cmd_name, "_provider": provider_hint}

            
            try_providers = (
                [provider_hint] if provider_hint != "auto" else ["opentofu", "tofu", "pulumi", "aws", "gcp", "azure", "noop"]
            )

            resolved_provider = None
            resolved_name = None
            resolved_meta = None
            last_err = None

            # try resolving across providers (stop on first success)
            for p in try_providers:
                try:
                    # try keyword call first
                    r = rr.resolve(resource=cmd_name, provider=p)
                    resolved_provider, resolved_name, resolved_meta = normalize_resolve_result(r)
                    break
                except TypeError:
                    # older resolver signature - try positional
                    try:
                        r = rr.resolve(cmd_name, p)
                        resolved_provider, resolved_name, resolved_meta = normalize_resolve_result(r)
                        break
                    except Exception as epos:
                        last_err = epos
                except ValueError as ve:
                    # ambiguous mapping/resolver returns ValueError with candidate info
                    last_err = ve
                    # continue trying other providers
                except Exception as e:
                    last_err = e
            
            # Default to opentofu if auto resolution failed or returned auto
            if not resolved_provider or resolved_provider == "auto":
                resolved_provider = "opentofu"

            # Build a resolved dict that will be included in every output
            resolved_block = {
                "_provider": resolved_provider,
                "_resolved": resolved_name,
            }
            if isinstance(resolved_meta, dict):
                # merge meta but keep core keys
                for k, v in resolved_meta.items():
                    if k not in resolved_block:
                        resolved_block[k] = v

            # If not resolved, return helpful diagnostic JSON
            if not resolved_meta:
                out = {
                    "mode": "dynamic-fallback",
                    "resource": cmd_name,
                    "name": name,
                    "params": params,
                    "resolved": resolved_block,
                    "error": f"could not resolve resource '{cmd_name}'",
                    "last_err": str(last_err) if last_err else None,
                }
                typer.echo(json.dumps(out, indent=2))
                return

            # Build canonical spec from resolver + CLI params
            spec: Dict[str, Any] = {
                "type": resolved_name.split(".")[-1] if isinstance(resolved_name, str) and "." in resolved_name else (resolved_name or cmd_name),
                "name": name,
                "provider": resolved_provider,
            }

            # merge CLI params, with simple casts
            for k, v in params.items():
                if isinstance(v, str) and v.isdigit():
                    spec[k] = int(v)
                elif isinstance(v, str) and v.lower() in ("true", "false"):
                    spec[k] = (v.lower() == "true")
                else:
                    spec[k] = v

            # attach resolver metadata for debugging / learning
            spec["_resolver_meta"] = resolved_meta

            # heuristics: whether resource is VM-like (use AutoscalerManager/Workflow)
            vm_like = any(t in str(spec["type"]).lower() for t in ("vm", "instance", "cluster", "node", "k8s", "eks", "gke", "aks"))

            # Async apply -> produce plan then enqueue an apply task
            if async_apply:
                # OffloadManager is commented out due to missing module
                # For now, we'll run the apply synchronously
                mgr = AutoscalerManager(db_path=DEFAULT_DB, provider=resolved_provider)
                res = mgr.run_once(name, spec, {"min": 1, "max": 1, "policy": [], "cooldown": 60}, observed_metrics={"cpu": 0}, plan_only=False)
                out = {
                    "mode": "dynamic-fallback",
                    "resource": cmd_name,
                    "name": name,
                    "params": params,
                    "resolved": resolved_block,
                    "result": res,
                    "note": "Async apply not available - OffloadManager module missing"
                }
                typer.echo(json.dumps(out, indent=2))
                return

            # Synchronous apply requested
            if yes:
                if vm_like:
                    mgr = AutoscalerManager(db_path=DEFAULT_DB, provider=resolved_provider)
                    res = mgr.run_once(name, spec, {"min": 1, "max": 1, "policy": [], "cooldown": 60}, observed_metrics={"cpu": 0}, plan_only=False)
                    out = {
                        "mode": "dynamic-fallback",
                        "resource": cmd_name,
                        "name": name,
                        "params": params,
                        "resolved": resolved_block,
                        "result": res,
                    }
                    typer.echo(json.dumps(out, indent=2))
                    return
                else:
                    # non-vm: route to Pulumi or OpenTofu adapters
                    if resolved_provider == "pulumi":
                        lines = list(pulumi_adapter.apply(spec, "dev"))
                        out = {
                            "mode": "dynamic-fallback",
                            "resource": cmd_name,
                            "name": name,
                            "params": params,
                            "resolved": resolved_block,
                            "result": {"apply_output": lines},
                        }
                        typer.echo(json.dumps(out, indent=2))
                        return
                    else:
                        # Default to OpenTofu
                        ta = OpenTofuAdapter()
                        res = ta.create_instance(name, spec, plan_only=False)
                        out = {
                            "mode": "dynamic-fallback",
                            "resource": cmd_name,
                            "name": name,
                            "params": params,
                            "resolved": resolved_block,
                            "result": res,
                        }
                        typer.echo(json.dumps(out, indent=2))
                        return

            # Default: plan-only behavior
            if vm_like:
                mgr = AutoscalerManager(db_path=DEFAULT_DB, provider=resolved_provider)
                res = mgr.run_once(name, spec, {"min": 1, "max": 1, "policy": [], "cooldown": 60}, observed_metrics={"cpu": 0}, plan_only=True)
                out = {
                    "mode": "dynamic-fallback",
                    "resource": cmd_name,
                    "name": name,
                    "params": params,
                    "resolved": resolved_block,
                    "result": res,
                }
                typer.echo(json.dumps(out, indent=2))
                return

            if resolved_provider == "pulumi":
                lines = list(pulumi_adapter.plan(spec, "dev"))
                out = {
                    "mode": "dynamic-fallback",
                    "resource": cmd_name,
                    "name": name,
                    "params": params,
                    "resolved": resolved_block,
                    "result": {"plan_output": lines},
                }
                typer.echo(json.dumps(out, indent=2))
                return

            # Default to OpenTofu
            ta = OpenTofuAdapter()
            res = ta.create_instance(name, spec, plan_only=True)
            out = {
                "mode": "dynamic-fallback",
                "resource": cmd_name,
                "name": name,
                "params": params,
                "resolved": resolved_block,
                "result": res,
            }
            typer.echo(json.dumps(out, indent=2))

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
# offload_app and pool_app are already created above

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


# -------------------------------------------------------------
#  Stack Commands (Blueprint-based Multi-resource Deployments)
# -------------------------------------------------------------
@app.command("stack")
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
        with open(spec, "r", encoding="utf-8") as fh:
            s = json.load(fh)
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
    # Policy Check
    # --------------------------------------------
    check_policy_or_die(s, skip=skip_policy)

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


@app.command()
def create(
    ec2_name: str = typer.Argument(...),
    spec: str = typer.Option(..., help="path to spec.json/yaml"),
    autoscale: Optional[str] = typer.Option(None, help="autoscale string like '1:3@cpu:70,60' or JSON"),
    provider: str = typer.Option("opentofu", help="provider adapter to use (noop|opentofu|pulumi|...)"),
    db_path: Optional[str] = typer.Option(DEFAULT_DB, help="path to SQLite DB (default cloudbrew.db)"),
    plan_only: bool = typer.Option(False, help="do not apply changes, only plan/dry-run"),
    metrics: Optional[str] = typer.Option(None, help='observed metrics as JSON'),
    offload: bool = typer.Option(False, help="enqueue heavy apply to offload worker instead of running inline"),
):
    with open(spec, "r", encoding="utf-8") as fh:
        s = json.load(fh)

    autoscale_cfg = (
        parse_autoscale_string(autoscale)
        if autoscale
        else {"min": s.get("count", 1), "max": s.get("count", 1), "policy": [], "cooldown": 60}
    )

    if metrics:
        try:
            observed = json.loads(metrics)
            if not isinstance(observed, dict):
                raise ValueError("metrics must be a JSON object")
        except Exception as e:
            raise typer.BadParameter(f"invalid --metrics JSON: {e}")
    else:
        observed = {"cpu": 10, "queue": 0}

    mgr = AutoscalerManager(db_path=db_path, provider=provider)

    if offload:
        off = OffloadManager(DEFAULT_OFFLOAD_DB)
        cmd = f"echo 'offloaded apply for {ec2_name} provider={provider}'"
        task_id = off.enqueue(adapter=provider, task_type="shell", payload={"cmd": cmd})
        typer.echo(json.dumps({"enqueued_task_id": task_id}))
        return

    res = mgr.run_once(ec2_name, s, autoscale_cfg, observed, plan_only=plan_only)
    typer.echo(json.dumps(res, indent=2))


# -------------------------------------------------------------
#  create-cluster (kept + integrated with OpenTofu/Pulumi)
# -------------------------------------------------------------
@app.command("create-cluster")
def create_cluster(
    name: str,
    provider: str = typer.Option("auto", help="opentofu | pulumi | aws | gcp | azure | auto"),
    region: str = typer.Option("us-east-1"),
    size: str = typer.Option("small"),
    yes: bool = typer.Option(False, "--yes", "-y", help="apply immediately"),
):
    """
    Create a cluster resource using OpenTofu or Pulumi.
    """
    spec = {"name": name, "type": "cluster", "region": region, "size": size}

    if provider == "auto":
        provider = "pulumi" if os.environ.get("PULUMI_HOME") else "opentofu"

    if provider == "pulumi":
        from LCF.cloud_adapters.pulumi_adapter import PulumiAdapter
        pa = PulumiAdapter()
        res = pa.create_instance(name, spec, plan_only=not yes)
    elif provider in ("opentofu", "tofu"):
        ta = OpenTofuAdapter()
        res = ta.create_instance(name, spec, plan_only=not yes)
    else:
        raise typer.BadParameter(f"Unsupported provider: {provider}")

    typer.echo(json.dumps(res, indent=2))


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
        ok = ta.destroy_instance(f"opentofu-{name}")
        typer.echo(json.dumps({"destroyed": ok, "name": name}))


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
    return destroy_vm(name, provider=provider, db_path=db_path,
                      offload=offload, offload_db=offload_db)


# -------------------------------------------------------------
#  Pulumi Helper Commands
# -------------------------------------------------------------
@app.command("pulumi-plan")
def cli_pulumi_plan(
    stack: str = typer.Option("dev"),
    spec_file: str = typer.Option("spec.json"),
):
    """
    Produce a Pulumi plan from a spec file.
    """
    p = Path(spec_file)
    if not p.exists():
        typer.secho(f"Spec file not found: {spec_file}", fg=typer.colors.RED)
        raise typer.Exit(2)

    spec = json.loads(p.read_text())

    for line in pulumi_adapter.plan(spec, stack):
        typer.echo(line)


@app.command("pulumi-apply")
def cli_pulumi_apply(
    stack: str = typer.Option("dev"),
    spec_file: str = typer.Option("spec.json"),
    offload: bool = typer.Option(False),
):
    """
    Apply Pulumi based on the given spec.
    """

    p = Path(spec_file)
    if not p.exists():
        typer.secho(f"Spec file not found: {spec_file}", fg=typer.colors.RED)
        raise typer.Exit(2)

    spec = json.loads(p.read_text())

    if offload:
        off = OffloadManager(DEFAULT_OFFLOAD_DB)
        tid = off.enqueue("pulumi", "apply_spec", {"spec": spec, "stack": stack})
        typer.echo(json.dumps({"enqueued_task_id": tid}))
        return

    for line in pulumi_adapter.apply(spec, stack):
        typer.echo(line)


@app.command("pulumi-destroy")
def cli_pulumi_destroy(
    stack: str = typer.Option("dev"),
    offload: bool = typer.Option(False),
):
    """
    Destroy a Pulumi stack.
    """
    if offload:
        off = OffloadManager(DEFAULT_OFFLOAD_DB)
        tid = off.enqueue("pulumi", "destroy_stack", {"stack": stack})
        typer.echo(json.dumps({"enqueued_task_id": tid}))
        return

    for line in pulumi_adapter.destroy(stack):
        typer.echo(line)


# -------------------------------------------------------------
#  Generic plan + apply-plan Commands (OpenTofu and Pulumi)
# -------------------------------------------------------------
@app.command("plan")
def plan_cmd(
    provider: str = typer.Option("opentofu"),
    spec_file: Optional[str] = typer.Option(None, "--spec-file", "-f"),
    spec_json: Optional[str] = typer.Option(None, "--spec"),
    db_path: Optional[str] = typer.Option(DEFAULT_DB),
):
    """
    Produce a plan for a given provider. Prints JSON summary.
    """

    if spec_file and spec_json:
        raise typer.BadParameter("Use only one of --spec-file or --spec")

    if spec_file:
        try:
            with open(spec_file, "r", encoding="utf-8") as fh:
                s = json.load(fh)
        except Exception as e:
            raise typer.Exit(f"Failed to load spec file: {e}")

    elif spec_json:
        try:
            s = json.loads(spec_json)
        except Exception as e:
            raise typer.BadParameter(f"Invalid --spec JSON: {e}")

    else:
        raise typer.BadParameter("Either --spec-file or --spec must be provided")

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
# Enhanced Schema-Driven Interactive Provisioning Commands
# -------------------------------------------------------------
@app.command("create")
def enhanced_create_command_wrapper(
    resource_name: str = typer.Argument(..., help="Resource name or type (e.g., aws_instance, myvm)"),
    provider: Optional[str] = typer.Option(None, help="Provider hint (aws, gcp, azure, etc.)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-confirm (skip interactive prompts)"),
):
    """
    Enhanced schema-driven interactive resource creation with automatic plan saving.
    
    This command implements the improved CloudBrew Schema-Driven Interactive Provisioning:
    - Extracts provider schemas from OpenTofu
    - Identifies required vs optional attributes
    - Prompts users only for required fields
    - Never guesses cloud values (AMIs, zones, etc.)
    - Automatically saves plans to files
    - Prompts whether to apply immediately (eliminating need for separate apply syntax)
    - Provides clear validation and confirmation workflows
    
    Example:
        cloudbrew create aws_instance myvm
        cloudbrew create aws_instance myvm --provider aws
    """
    return enhanced_create_command(resource_name, provider, yes)

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
    Enqueue an async task such as:
    - apply_plan
    - destroy
    - shell
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
    Run the async task worker that executes queued apply/destroy operations.
    """
    off = OffloadManager(db_path)
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
    from LCF import store
    st = store.SQLiteStore(db_path)
    instances = st.list_instances()
    typer.echo(json.dumps({"instances": instances}, indent=2))








# -------------------------------------------------------------
# CLI Entrypoint
# -------------------------------------------------------------
if __name__ == "__main__":
    app()