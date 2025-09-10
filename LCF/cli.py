# LCF/cli.py
import json
import os
from pathlib import Path
from typing import Optional

import typer

from LCF.autoscaler import AutoscalerManager, parse_autoscale_string
from LCF.offload.manager import OffloadManager
from LCF.cloud_adapters import get_compute_adapter
from LCF.cloud_adapters import pulumi_adapter  # Pulumi adapter wiring
from LCF.cloud_adapters.terraform_adapter import TerraformAdapter


app = typer.Typer()
offload_app = typer.Typer()
app.add_typer(offload_app, name="offload")

DEFAULT_DB = "cloudbrew.db"
DEFAULT_OFFLOAD_DB = "cloudbrew_offload.db"


# -------------------------
# Generic existing create kept
# -------------------------
@app.command()
def create(
    ec2_name: str = typer.Argument(...),
    spec: str = typer.Option(..., help="path to spec.json/yaml"),
    autoscale: Optional[str] = typer.Option(None, help="autoscale string like '1:3@cpu:70,60' or JSON"),
    provider: str = typer.Option("noop", help="provider adapter to use (noop|terraform|pulumi|...)"),
    db_path: Optional[str] = typer.Option(DEFAULT_DB, help="path to SQLite DB (default cloudbrew.db)"),
    plan_only: bool = typer.Option(False, help="do not apply changes, only plan/dry-run"),
    metrics: Optional[str] = typer.Option(None, help='observed metrics as JSON, e.g. \'{"cpu": 80}\''),  # noqa
    offload: bool = typer.Option(False, help="enqueue heavy apply to offload worker instead of running inline"),
):
    with open(spec, "r") as fh:
        s = json.load(fh)

    autoscale_cfg = (
        parse_autoscale_string(autoscale)
        if autoscale
        else {
            "min": s.get("count", 1),
            "max": s.get("count", 1),
            "policy": [],
            "cooldown": 60,
        }
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


# -------------------------
# New: vendor-neutral create vm
# -------------------------
@app.command("create-vm")
def create_vm(
    name: str = typer.Argument(..., help="logical name for the VM"),
    spec: Optional[str] = typer.Option(None, help="optional JSON spec path; if provided overrides flags"),
    image: Optional[str] = typer.Option(None, help="canonical image name (eg ubuntu-22.04)"),
    size: Optional[str] = typer.Option("small", help="canonical size (small|medium|large)"),
    region: Optional[str] = typer.Option("us-east-1", help="region"),
    count: int = typer.Option(1, help="instance count"),
    provider: str = typer.Option("terraform", help="provider (terraform|pulumi|aws|gcp|azure|auto)"),
    db_path: Optional[str] = typer.Option(DEFAULT_DB, help="path to SQLite DB"),
    yes: bool = typer.Option(False, "--yes", "-y", help="apply (plan+apply) synchronously"),
    async_apply: bool = typer.Option(False, "--async", help="enqueue apply to offload worker and return immediately"),
    offload_db: str = typer.Option(DEFAULT_OFFLOAD_DB, help="offload DB path"),
):
    """
    Create a VM across providers with a single canonical spec.
    Default: plan-only (safe). Use --yes to apply or --async to enqueue.
    """
    if spec:
        with open(spec, "r") as fh:
            s = json.load(fh)
    else:
        s = {
            "name": name,
            "type": "vm",
            "image": image,
            "size": size,
            "region": region,
            "count": count,
        }

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
        task_payload = {"plan_path": plan_id}
        tid = off.enqueue(adapter=chosen_provider, task_type="apply_plan", payload=task_payload)
        typer.echo(json.dumps({"enqueued_task_id": tid, "plan_id": plan_id}, indent=2))
        return

    if yes:
        res = mgr.run_once(name, s, autoscale_cfg, observed_metrics={"cpu": 0}, plan_only=False)
        typer.echo(json.dumps(res, indent=2))
        return

    res = mgr.run_once(name, s, autoscale_cfg, observed_metrics={"cpu": 0}, plan_only=True)
    typer.echo(json.dumps(res, indent=2))


# -------------------------
# Destroy VM command
# -------------------------
@app.command("destroy-vm")
def destroy_vm(
    name: str = typer.Argument(..., help="logical name for the VM to destroy"),
    provider: str = typer.Option("terraform", help="provider (terraform|pulumi|auto)"),
    db_path: Optional[str] = typer.Option(DEFAULT_DB, help="path to SQLite DB"),
    offload: bool = typer.Option(False, "--offload", help="enqueue destroy to OffloadManager"),
    offload_db: str = typer.Option(DEFAULT_OFFLOAD_DB, help="offload DB path"),
):
    """
    Destroy a VM (supports terraform destroy and pulumi destroy_stack).
    """
    if offload:
        off = OffloadManager(offload_db)
        if provider == "pulumi":
            tid = off.enqueue(adapter="pulumi", task_type="destroy_stack", payload={"stack": name})
        else:
            tid = off.enqueue(adapter="terraform", task_type="destroy", payload={"adapter_id": f"terraform-{name}"})
        typer.echo(json.dumps({"enqueued_task_id": tid}))
        return

    if provider == "pulumi":
        for line in pulumi_adapter.destroy(name):
            typer.echo(line)
    else:
        ta = TerraformAdapter(db_path)
        ok = ta.destroy_instance(f"terraform-{name}")
        typer.echo(json.dumps({"destroyed": ok, "name": name}))

# -------------------------
# Destroy alias for ergonomics
# -------------------------
@app.command("destroy")
def destroy_alias(
    name: str = typer.Argument(..., help="logical name for the VM to destroy"),
    provider: str = typer.Option("terraform", help="provider (terraform|pulumi|auto)"),
    db_path: Optional[str] = typer.Option(DEFAULT_DB, help="path to SQLite DB"),
    offload: bool = typer.Option(False, "--offload", help="enqueue destroy to OffloadManager"),
    offload_db: str = typer.Option(DEFAULT_OFFLOAD_DB, help="offload DB path"),
):
"""
Alias for `destroy-vm`. Usage: `cloudbrew destroy <name>`.
"""
return destroy_vm(name, provider=provider, db_path=db_path, offload=offload, offload_db=offload_db)



# -------------------------
# Pulumi helper commands
# -------------------------
@app.command("pulumi-plan")
def cli_pulumi_plan(stack: str = typer.Option("dev", help="stack name"),
                    spec_file: str = typer.Option("spec.json", help="path to spec JSON")):
    """
    Run pulumi preview (plan) against a spec file and stream logs.
    """
    p = Path(spec_file)
    if not p.exists():
        typer.secho(f"Spec file not found: {spec_file}", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    try:
        spec = json.loads(p.read_text())
    except Exception as e:
        typer.secho(f"Failed to load spec file: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=3)

    for line in pulumi_adapter.plan(spec, stack):
        typer.echo(line)


@app.command("pulumi-apply")
def cli_pulumi_apply(stack: str = typer.Option("dev", help="stack name"),
                     spec_file: str = typer.Option("spec.json", help="path to spec JSON"),
                     offload: bool = typer.Option(False, help="enqueue apply to offload worker")):
    """
    Apply a spec via Pulumi. Use --offload to enqueue apply to OffloadManager.
    """
    p = Path(spec_file)
    if not p.exists():
        typer.secho(f"Spec file not found: {spec_file}", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    try:
        spec = json.loads(p.read_text())
    except Exception as e:
        typer.secho(f"Failed to load spec file: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=3)

    if offload:
        off = OffloadManager(DEFAULT_OFFLOAD_DB)
        tid = off.enqueue(adapter="pulumi", task_type="apply_spec", payload={"spec": spec, "stack": stack})
        typer.echo(json.dumps({"enqueued_task_id": tid}))
        return

    for line in pulumi_adapter.apply(spec, stack):
        typer.echo(line)


@app.command("pulumi-destroy")
def cli_pulumi_destroy(stack: str = typer.Option("dev", help="stack name"),
                       offload: bool = typer.Option(False, help="enqueue destroy to offload worker")):
    """
    Destroy a Pulumi stack. Use --offload to enqueue the destroy to the OffloadManager.
    """
    if offload:
        off = OffloadManager(DEFAULT_OFFLOAD_DB)
        tid = off.enqueue(adapter="pulumi", task_type="destroy_stack", payload={"stack": stack})
        typer.echo(json.dumps({"enqueued_task_id": tid}))
        return

    for line in pulumi_adapter.destroy(stack):
        typer.echo(line)


# -------------------------
# Generic plan + apply-plan commands
# -------------------------
@app.command("plan")
def plan_cmd(
    provider: str = typer.Option("terraform", help="provider (terraform|pulumi)"),
    spec_file: Optional[str] = typer.Option(None, "--spec-file", "-f", help="path to JSON spec file"),
    spec_json: Optional[str] = typer.Option(None, "--spec", help="inline JSON spec (mutually exclusive with --spec-file)"),
    db_path: Optional[str] = typer.Option(DEFAULT_DB, help="path to SQLite DB"),
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
    provider: str = typer.Option("terraform", help="provider (terraform|pulumi)"),
    plan_id: str = typer.Option(..., help="plan identifier (path for terraform)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="apply synchronously (default is plan-only)"),
    async_apply: bool = typer.Option(False, "--async", help="enqueue apply to offload worker and return immediately"),
    offload_db: str = typer.Option(DEFAULT_OFFLOAD_DB, help="offload DB path"),
):
    """
    Apply a previously saved plan produced by `cloudbrew plan`.
    """
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
