# LCF/cli.py
"""
Cloudbrew CLI entrypoint.

Provides:
- Static commands: create, create-vm, destroy-vm, destroy, plan, apply-plan, pulumi helpers, offload.
- Dynamic fallback: unknown top-level verbs (e.g. `cloudbrew bucket ...`) are handled by ResourceResolver
  via a custom Typer group (CloudbrewGroup) and routed to appropriate adapters/managers.
"""

from __future__ import annotations

import json
import os
import sys
import click
from pathlib import Path
from typing import Optional, List, Dict, Any

import typer
from typer.core import TyperGroup

# Local imports (adapters/managers)
from LCF.resource_resolver import ResourceResolver
from LCF.autoscaler import AutoscalerManager, parse_autoscale_string
from LCF.offload.manager import OffloadManager
from LCF.cloud_adapters import pulumi_adapter
from LCF.cloud_adapters.terraform_adapter import TerraformAdapter

# -------------------------
# Constants / defaults
# -------------------------
DEFAULT_DB = "cloudbrew.db"
DEFAULT_OFFLOAD_DB = "cloudbrew_offload.db"


# -------------------------
# Dynamic command fallback group
# -------------------------
class CloudbrewGroup(TyperGroup):
    """Custom Typer group with dynamic command fallback."""

    def get_command(self, ctx, cmd_name: str):
        cmd = super().get_command(ctx, cmd_name)
        if cmd:
            return cmd

        def dynamic_command(_args):
            """
            Handles unknown top-level verbs by:
            - parsing CLI tokens (name + --key value flags)
            - asking ResourceResolver to map the short token (cmd_name) to provider/canonical resource
            - building a canonical spec and then choosing plan/apply behavior (sync/async) routed to adapters
            All output is returned as JSON; dynamic responses include "mode": "dynamic-fallback".
            """
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

            def normalize_resolve_result(res) -> (str, str, Dict[str, Any]):
                """
                Normalize different shapes of resolver results into:
                (provider, canonical_resource_name, metadata_dict)
                """
                if isinstance(res, dict):
                    provider = res.get("_provider") or provider_hint
                    resolved = res.get("_resolved") or res.get("resource") or res.get("name") or cmd_name
                    return str(provider), str(resolved), res
                if isinstance(res, str):
                    return provider_hint, res, {"_resolved": res, "_provider": provider_hint}
                # fallback
                return provider_hint, cmd_name, {"_resolved": cmd_name, "_provider": provider_hint}

            # candidate providers to try
            try_providers = (
                [provider_hint] if provider_hint != "auto" else ["terraform", "pulumi", "aws", "gcp", "azure", "noop"]
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

            # heuristics: whether resource is VM-like (use AutoscalerManager/TerraformWorkflow)
            vm_like = any(t in str(spec["type"]).lower() for t in ("vm", "instance", "cluster", "node", "k8s", "eks", "gke", "aks"))

            # Async apply -> produce plan then enqueue an apply task
            if async_apply:
                mgr = AutoscalerManager(db_path=DEFAULT_DB, provider=resolved_provider)
                plan_res = mgr.run_once(name, spec, {"min": 1, "max": 1, "policy": [], "cooldown": 60}, observed_metrics={"cpu": 0}, plan_only=True)
                plan_id = None
                for a in plan_res.get("actions", []):
                    plan_id = a.get("res", {}).get("plan_id") or plan_id
                off = OffloadManager(DEFAULT_OFFLOAD_DB)
                # adapter name in offload queue should match provider (e.g. terraform/pulumi)
                tid = off.enqueue(adapter=resolved_provider, task_type="apply_plan", payload={"plan_path": plan_id})
                out = {
                    "mode": "dynamic-fallback",
                    "resource": cmd_name,
                    "name": name,
                    "params": params,
                    "resolved": resolved_block,
                    "enqueued_task_id": tid,
                    "plan_id": plan_id,
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
                    # non-vm: route to Pulumi or Terraform adapters
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
                        ta = TerraformAdapter()
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

            ta = TerraformAdapter()
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


# -------------------------
# App setup
# -------------------------
app = typer.Typer(cls=CloudbrewGroup)
offload_app = typer.Typer()
app.add_typer(offload_app, name="offload")


# -------------------------
# Static commands
# -------------------------
@app.command()
def create(
    ec2_name: str = typer.Argument(...),
    spec: str = typer.Option(..., help="path to spec.json/yaml"),
    autoscale: Optional[str] = typer.Option(None, help="autoscale string like '1:3@cpu:70,60' or JSON"),
    provider: str = typer.Option("noop", help="provider adapter to use (noop|terraform|pulumi|...)"),
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


@app.command("create-vm")
def create_vm(
    name: str,
    spec: Optional[str] = typer.Option(None),
    image: Optional[str] = typer.Option(None),
    size: Optional[str] = typer.Option("small"),
    region: Optional[str] = typer.Option("us-east-1"),
    count: int = typer.Option(1),
    provider: str = typer.Option("terraform"),
    db_path: Optional[str] = typer.Option(DEFAULT_DB),
    yes: bool = typer.Option(False, "--yes", "-y"),
    async_apply: bool = typer.Option(False, "--async"),
    offload_db: str = typer.Option(DEFAULT_OFFLOAD_DB),
):
    if spec:
        with open(spec, "r", encoding="utf-8") as fh:
            s = json.load(fh)
    else:
        s = {"name": name, "type": "vm", "image": image, "size": size, "region": region, "count": count}

    chosen_provider = provider
    if provider == "auto":
        if os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"):
            chosen_provider = "aws"
        else:
            chosen_provider = "terraform"

    autoscale_cfg = {"min": s.get("count", count), "max": s.get("count", count), "policy": [], "cooldown": 60}
    mgr = AutoscalerManager(db_path=db_path, provider=chosen_provider)

    if async_apply:
        res = mgr.run_once(name, s, autoscale_cfg, observed_metrics={"cpu": 0}, plan_only=True)
        plan_id = None
        for a in res.get("actions", []):
            plan_id = a.get("res", {}).get("plan_id") or plan_id
        off = OffloadManager(offload_db)
        tid = off.enqueue(adapter=chosen_provider, task_type="apply_plan", payload={"plan_path": plan_id})
        typer.echo(json.dumps({"enqueued_task_id": tid, "plan_id": plan_id}, indent=2))
        return

    if yes:
        res = mgr.run_once(name, s, autoscale_cfg, observed_metrics={"cpu": 0}, plan_only=False)
        typer.echo(json.dumps(res, indent=2))
        return

    res = mgr.run_once(name, s, autoscale_cfg, observed_metrics={"cpu": 0}, plan_only=True)
    typer.echo(json.dumps(res, indent=2))


@app.command("create-cluster")
def create_cluster(
    name: str,
    provider: str = typer.Option("auto", help="terraform | pulumi | aws | gcp | azure | auto"),
    region: str = typer.Option("us-east-1"),
    size: str = typer.Option("small"),
    yes: bool = typer.Option(False, "--yes", "-y", help="apply immediately"),
):
    spec = {"name": name, "type": "cluster", "region": region, "size": size}
    if provider == "auto":
        provider = "pulumi" if os.environ.get("PULUMI_HOME") else "terraform"

    if provider == "pulumi":
        from LCF.cloud_adapters.pulumi_adapter import PulumiAdapter
        pa = PulumiAdapter()
        res = pa.create_instance(name, spec, plan_only=not yes)
    elif provider == "terraform":
        ta = TerraformAdapter()
        res = ta.create_instance(name, spec, plan_only=not yes)
    else:
        raise typer.BadParameter(f"unsupported provider: {provider}")

    typer.echo(json.dumps(res, indent=2))


@app.command("destroy-vm")
def destroy_vm(
    name: str,
    provider: str = typer.Option("terraform"),
    db_path: Optional[str] = typer.Option(DEFAULT_DB),
    offload: bool = typer.Option(False),
    offload_db: str = typer.Option(DEFAULT_OFFLOAD_DB),
):
    if offload:
        off = OffloadManager(offload_db)
        tid = (
            off.enqueue("pulumi", "destroy_stack", {"stack": name})
            if provider == "pulumi"
            else off.enqueue("terraform", "destroy", {"adapter_id": f"terraform-{name}"})
        )
        typer.echo(json.dumps({"enqueued_task_id": tid}))
        return

    if provider == "pulumi":
        for line in pulumi_adapter.destroy(name):
            typer.echo(line)
    else:
        ta = TerraformAdapter(db_path)
        ok = ta.destroy_instance(f"terraform-{name}")
        typer.echo(json.dumps({"destroyed": ok, "name": name}))


@app.command("destroy")
def destroy_alias(
    name: str,
    provider: str = typer.Option("terraform"),
    db_path: Optional[str] = typer.Option(DEFAULT_DB),
    offload: bool = typer.Option(False),
    offload_db: str = typer.Option(DEFAULT_OFFLOAD_DB),
):
    return destroy_vm(name, provider=provider, db_path=db_path, offload=offload, offload_db=offload_db)


# -------------------------
# Pulumi helper commands
# -------------------------
@app.command("pulumi-plan")
def cli_pulumi_plan(stack: str = typer.Option("dev"), spec_file: str = typer.Option("spec.json")):
    p = Path(spec_file)
    if not p.exists():
        typer.secho(f"Spec file not found: {spec_file}", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    spec = json.loads(p.read_text())
    for line in pulumi_adapter.plan(spec, stack):
        typer.echo(line)


@app.command("pulumi-apply")
def cli_pulumi_apply(stack: str = typer.Option("dev"), spec_file: str = typer.Option("spec.json"), offload: bool = typer.Option(False)):
    p = Path(spec_file)
    if not p.exists():
        typer.secho(f"Spec file not found: {spec_file}", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    spec = json.loads(p.read_text())
    if offload:
        off = OffloadManager(DEFAULT_OFFLOAD_DB)
        tid = off.enqueue("pulumi", "apply_spec", {"spec": spec, "stack": stack})
        typer.echo(json.dumps({"enqueued_task_id": tid}))
        return
    for line in pulumi_adapter.apply(spec, stack):
        typer.echo(line)


@app.command("pulumi-destroy")
def cli_pulumi_destroy(stack: str = typer.Option("dev"), offload: bool = typer.Option(False)):
    if offload:
        off = OffloadManager(DEFAULT_OFFLOAD_DB)
        tid = off.enqueue("pulumi", "destroy_stack", {"stack": stack})
        typer.echo(json.dumps({"enqueued_task_id": tid}))
        return
    for line in pulumi_adapter.destroy(stack):
        typer.echo(line)


# -------------------------
# Generic plan + apply-plan commands
# -------------------------
@app.command("plan")
def plan_cmd(
    provider: str = typer.Option("terraform"),
    spec_file: Optional[str] = typer.Option(None, "--spec-file", "-f"),
    spec_json: Optional[str] = typer.Option(None, "--spec"),
    db_path: Optional[str] = typer.Option(DEFAULT_DB),
):
    """
    Produce a plan for a given provider. Prints JSON summary.
    """
    if spec_file and spec_json:
        raise typer.BadParameter("use only one of --spec-file or --spec")

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
            raise typer.BadParameter(f"invalid --spec JSON: {e}")
    else:
        # <-- EXACT expected error message for tests
        raise typer.BadParameter("either --spec-file or --spec must be provided")

    if provider == "terraform":
        ta = TerraformAdapter(db_path=db_path)
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
        raise typer.BadParameter(f"unsupported provider: {provider}")


@app.command("apply-plan")
def apply_plan_cmd(
    provider: str = typer.Option("terraform"),
    plan_id: str = typer.Option(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
    async_apply: bool = typer.Option(False, "--async"),
    offload_db: str = typer.Option(DEFAULT_OFFLOAD_DB),
):
    if async_apply:
        off = OffloadManager(offload_db)
        payload = {"plan_id": plan_id}
        tid = off.enqueue(provider, "apply_plan", payload)
        typer.echo(json.dumps({"enqueued_task_id": tid, "plan_id": plan_id}, indent=2))
        return

    if provider == "terraform":
        ta = TerraformAdapter()
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
        raise typer.BadParameter(f"unsupported provider: {provider}")


# -------------------------
# Offload and status (kept)
# -------------------------
@offload_app.command("enqueue")
def offload_enqueue(adapter: str = typer.Option("terraform"), task_type: str = typer.Option(...), payload: str = typer.Option("{}", help="JSON payload")):
    off = OffloadManager()
    p = json.loads(payload)
    tid = off.enqueue(adapter=adapter, task_type=task_type, payload=p)
    typer.echo(json.dumps({"task_id": tid}))


@offload_app.command("run-worker")
def offload_run_worker(db_path: str = typer.Option(DEFAULT_OFFLOAD_DB), poll_interval: int = typer.Option(5), concurrency: int = typer.Option(1)):
    off = OffloadManager(db_path)
    try:
        off.run_worker(poll_interval=poll_interval, concurrency=concurrency)
    except KeyboardInterrupt:
        off.stop()


@app.command()
def status(db_path: Optional[str] = typer.Option(DEFAULT_DB, help="path to SQLite DB (default cloudbrew.db)")):
    from LCF import store
    st = store.SQLiteStore(db_path)
    instances = st.list_instances()
    typer.echo(json.dumps({"instances": instances}, indent=2))


if __name__ == "__main__":
    app()
