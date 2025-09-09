# LCF/cli.py
import json
import os
from typing import Optional

import typer

from LCF.autoscaler import AutoscalerManager, parse_autoscale_string
from LCF.offload.manager import OffloadManager
from LCF.cloud_adapters import get_compute_adapter

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
    metrics: Optional[str] = typer.Option(None, help='observed metrics as JSON, e.g. \'{"cpu": 80}\''),
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
    # Build canonical spec
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

    # provider auto-resolution: if provider == auto, choose terraform by default
    chosen_provider = provider
    if provider == "auto":
        # naive detection: AWS env vars present -> aws, else terraform
        if os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"):
            chosen_provider = "aws"
        else:
            chosen_provider = "terraform"

    # autoscale defaults (no autoscale by default)
    autoscale_cfg = {"min": s.get("count", count), "max": s.get("count", count), "policy": [], "cooldown": 60}

    mgr = AutoscalerManager(db_path=db_path, provider=chosen_provider)

    # async apply: create plan, enqueue apply_plan task
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

    # sync apply if --yes
    if yes:
        res = mgr.run_once(name, s, autoscale_cfg, observed_metrics={"cpu": 0}, plan_only=False)
        typer.echo(json.dumps(res, indent=2))
        return

    # default: plan-only
    res = mgr.run_once(name, s, autoscale_cfg, observed_metrics={"cpu": 0}, plan_only=True)
    typer.echo(json.dumps(res, indent=2))


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
