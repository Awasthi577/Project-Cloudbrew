# cloudbrew/cli_configure.py
from __future__ import annotations
import json
import os
import sys
import getpass
import pathlib
import typing as t

import typer

try:
    import keyring
except Exception:
    keyring = None  # fallback will be used

app = typer.Typer(help="CloudBrew interactive init/configure")

CONFIG_DIR = pathlib.Path.home() / ".cloudbrew"
CONFIG_PATH = CONFIG_DIR / "config.json"

def ensure_config_dir():
    CONFIG_DIR.mkdir(mode=0o700, exist_ok=True)

def save_config(config: dict):
    ensure_config_dir()
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    os.chmod(CONFIG_PATH, 0o600)

def store_secret(key: str, secret: str):
    if keyring:
        keyring.set_password("cloudbrew", key, secret)
        return {"method": "keyring", "key": key}
    else:
        # fallback - store encrypted file (simple base64 for example — replace with Fernet in prod)
        import base64
        ensure_config_dir()
        enc_path = CONFIG_DIR / f"{key}.secret"
        enc_path.write_bytes(base64.b64encode(secret.encode("utf-8")))
        os.chmod(enc_path, 0o600)
        return {"method": "file", "path": str(enc_path)}

def get_secret_meta(key: str):
    # return metadata showing where secret stored
    # not fetching secret value here for security
    if keyring:
        return {"method": "keyring", "key": key}
    else:
        path = CONFIG_DIR / f"{key}.secret"
        if path.exists():
            return {"method": "file", "path": str(path)}
    return None

def validate_aws(access_key: str, secret_key: str, region: str | None):
    """Do a light validation; if boto3 not installed, skip validation."""
    try:
        import boto3
        client = boto3.client(
            "sts",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region or "us-east-1",
        )
        # lightweight auth check
        client.get_caller_identity()
        return True, "AWS credentials validated (STS get-caller-identity succeeded)."
    except Exception as e:
        return False, f"AWS validation failed: {e}"

def validate_gcp(sa_json_path: str):
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds = service_account.Credentials.from_service_account_file(sa_json_path)
        svc = build("project", "v1", credentials=creds)  # may not exist depending on libs
        # we won't call heavy APIs; assume service_account_credentials loaded means ok
        return True, "GCP service account JSON loaded."
    except Exception as e:
        return False, f"GCP validation skipped or failed: {e}"

def validate_azure(tenant_id: str, client_id: str, client_secret: str):
    try:
        from azure.identity import ClientSecretCredential
        credential = ClientSecretCredential(tenant_id, client_id, client_secret)
        # try to get a token for management
        token = credential.get_token("https://management.azure.com/.default")
        if token.token:
            return True, "Azure credentials validated (token acquired)."
        return False, "Azure validation failed: no token"
    except Exception as e:
        return False, f"Azure validation skipped or failed: {e}"

@app.command()
def init(interactive: bool = True):
    """
    Interactive init: prompts for provider and credentials and stores them securely.
    """
    ensure_config_dir()

    typer.echo("Welcome to CloudBrew configuration.")
    providers = ["aws", "gcp", "azure", "none"]
    choice = typer.prompt("Select provider (aws/gcp/azure/none)", default="none")
    choice = choice.strip().lower()
    if choice not in providers:
        typer.secho("Invalid provider selected. Exiting.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    config: dict = {"default_provider": choice, "creds": {}}

    if choice == "aws":
        access_key = typer.prompt("AWS Access Key ID")
        secret_key = getpass.getpass("AWS Secret Access Key: ")
        region = typer.prompt("Default region (optional)", default="")
        ok, msg = validate_aws(access_key, secret_key, region or None)
        typer.echo(msg)
        secret_meta = store_secret("aws_secret_key", secret_key)
        config["creds"]["aws"] = {
            "access_key_id": access_key,
            "secret_meta": secret_meta,
            "region": region or None,
        }

    elif choice == "gcp":
        typer.echo("Provide path to your GCP service account JSON file (preferred).")
        sa_path = typer.prompt("Service account JSON path")
        ok, msg = validate_gcp(sa_path)
        typer.echo(msg)
        # store path in config (not secret) and optionally store file content in keyring if desired
        config["creds"]["gcp"] = {"service_account_path": sa_path}

    elif choice == "azure":
        tenant = typer.prompt("Azure Tenant ID")
        client = typer.prompt("Azure Client ID")
        secret = getpass.getpass("Azure Client Secret: ")
        subscription = typer.prompt("Azure Subscription ID (optional)", default="")
        ok, msg = validate_azure(tenant, client, secret)
        typer.echo(msg)
        secret_meta = store_secret("azure_client_secret", secret)
        config["creds"]["azure"] = {
            "tenant_id": tenant,
            "client_id": client,
            "client_secret_meta": secret_meta,
            "subscription_id": subscription or None,
        }

    else:
        typer.echo("Configured for no cloud (noop).")

    save_config(config)
    typer.secho("Configuration saved to ~/.cloudbrew/config.json", fg=typer.colors.GREEN)
    typer.secho(f"Default provider set to `{choice}`", fg=typer.colors.GREEN)

if __name__ == "__main__":
    app()
