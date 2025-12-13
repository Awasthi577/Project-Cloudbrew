# cloudbrew/cli_init.py
from __future__ import annotations
import json
import os
import pathlib
import getpass
import typing as t

import typer

from .secret_store import SecretStore, CONFIG_DIR

app = typer.Typer(help="CloudBrew interactive init/configure")
CONFIG_PATH = CONFIG_DIR / "config.json"

store = SecretStore()

PROVIDERS = ["aws", "gcp", "azure", "none"]


def _save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(mode=0o700, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    os.chmod(CONFIG_PATH, 0o600)


def _load_config() -> dict | None:
    if not CONFIG_PATH.exists():
        return None
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return None


def _validate_aws(access_key: str, secret: str, region: t.Optional[str]) -> tuple[bool, str]:
    try:
        import boto3
        client = boto3.client(
            "sts",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret,
            region_name=region or "us-east-1",
        )
        client.get_caller_identity()
        return True, "AWS validation succeeded (sts get-caller-identity)."
    except Exception as e:
        return False, f"AWS validation failed: {e}"


def _validate_gcp(sa_path: str) -> tuple[bool, str]:
    try:
        # lightweight parse-only check
        from google.oauth2 import service_account
        service_account.Credentials.from_service_account_file(sa_path)
        return True, "GCP service account JSON parsed."
    except Exception as e:
        return False, f"GCP validation failed/skipped: {e}"


def _validate_azure(tenant: str, client: str, secret: str) -> tuple[bool, str]:
    try:
        from azure.identity import ClientSecretCredential
        cred = ClientSecretCredential(tenant, client, secret)
        cred.get_token("https://management.azure.com/.default")
        return True, "Azure validation succeeded (token acquired)."
    except Exception as e:
        return False, f"Azure validation failed/skipped: {e}"


@app.command()
def init(yes: bool = typer.Option(False, "--yes", help="Skip interactive prompts and set provider=none")):
    """Interactive configuration for CloudBrew. Use --yes to skip and set provider=none."""
    if yes:
        cfg = {"default_provider": "none", "creds": {}}
        _save_config(cfg)
        typer.secho("Initialized with provider=none (non-interactive).", fg=typer.colors.GREEN)
        raise typer.Exit()

    typer.echo("Welcome to CloudBrew configuration (interactive).\n")
    choice = typer.prompt("Select provider (aws/gcp/azure/none)", default="none").strip().lower()
    if choice not in PROVIDERS:
        typer.secho("Invalid choice, aborting.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    cfg: dict = {"default_provider": choice, "creds": {}}

    if choice == "aws":
        ak = typer.prompt("AWS Access Key ID")
        sk = getpass.getpass("AWS Secret Access Key: ")
        region = typer.prompt("Default region (optional)", default="")
        ok, msg = _validate_aws(ak, sk, region or None)
        typer.echo(msg)
        meta = store.store_secret("aws_secret_key", sk)
        cfg["creds"]["aws"] = {
            "access_key_id": ak,
            "secret_meta": meta,
            "region": region or None,
        }

    elif choice == "gcp":
        typer.echo("Provide path to GCP service account JSON file (preferred).")
        path = typer.prompt("Service account JSON path")
        project_id = typer.prompt("Google Cloud Project ID")
        region = typer.prompt("Default Region", default="us-central1")
        ok, msg = _validate_gcp(path)
        typer.echo(msg)
        cfg["creds"]["gcp"] = {"service_account_path": path, "project": project_id, "region": region}

    elif choice == "azure":
        tenant = typer.prompt("Azure Tenant ID")
        client = typer.prompt("Azure Client ID")
        secret = getpass.getpass("Azure Client Secret: ")
        sub = typer.prompt("Subscription ID (optional)", default="")
        ok, msg = _validate_azure(tenant, client, secret)
        typer.echo(msg)
        meta = store.store_secret("azure_client_secret", secret)
        cfg["creds"]["azure"] = {
            "tenant_id": tenant,
            "client_id": client,
            "client_secret_meta": meta,
            "subscription_id": sub or None,
        }

    else:
        typer.echo("Configured for no provider (noop).\n")

    _save_config(cfg)
    typer.secho(f"Configuration saved to {CONFIG_PATH}", fg=typer.colors.GREEN)
    typer.secho(f"Default provider set to '{choice}'", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
