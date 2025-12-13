# cloudbrew/bootstrap.py
from __future__ import annotations
import json
import threading
import typing as t
import pathlib
import os

from .secret_store import SecretStore, CONFIG_DIR

CONFIG_PATH = CONFIG_DIR / "config.json"
store = SecretStore()


def load_config() -> dict | None:
    local_path = pathlib.Path("config.json")
    if local_path.exists():
        try:
            return json.loads(local_path.read_text(encoding="utf-8"))
        except Exception:
            pass 

    # 2. Check Home Directory
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return None
            
    return None


def validate_provider(cfg: dict) -> dict:
    """
    Synchronously validate the configured provider.
    Returns a result dict:
      {
        "ok": bool,
        "provider": "aws"|"gcp"|"azure"|...,
        "fallback": bool,        # True when validation failed and caller should consider noop
        "notice": str,           # human-friendly message
      }
    """
    result: dict = {"ok": True, "provider": None, "fallback": False, "notice": None}
    try:
        # Default to 'gcp' if not specified but gcp creds exist
        creds = cfg.get("creds", {})
        provider = cfg.get("default_provider")
        
        # Auto-detect provider if missing
        if not provider:
            if "gcp" in creds: provider = "gcp"
            elif "aws" in creds: provider = "aws"
            elif "azure" in creds: provider = "azure"
            else: provider = "noop"

        result["provider"] = provider

        if provider == "aws":
            meta = creds.get("aws") or {}
            access = meta.get("access_key_id")
            secret_meta = meta.get("secret_meta") or {}
            secret_key_name = secret_meta.get("key") or "aws_secret_key"
            secret = store.retrieve_secret(secret_key_name)
            # Lightweight validation using boto3 if available
            try:
                import boto3  # type: ignore
                client = boto3.client(
                    "sts",
                    aws_access_key_id=access,
                    aws_secret_access_key=secret,
                )
                client.get_caller_identity()
                result["notice"] = "AWS credentials valid"
            except Exception as e:
                result.update({"ok": False, "fallback": True, "notice": f"AWS validation failed: {e}"})

        elif provider == "gcp":
            # --- UPDATED GCP LOGIC ---
            meta = creds.get("gcp") or {}
            sa_path = meta.get("service_account_path")
            
            if not sa_path or not os.path.exists(sa_path):
                result.update({"ok": False, "fallback": True, "notice": f"GCP service account file not found: {sa_path}"})
            else:
                try:
                    # Lightweight validation: check if file is valid JSON
                    with open(sa_path, 'r') as f:
                        json.load(f)
                    result["notice"] = "GCP service account JSON valid"
                except Exception as e:
                    result.update({"ok": False, "fallback": True, "notice": f"GCP validation failed: {e}"})
            # -------------------------

        elif provider == "azure":
            meta = creds.get("azure") or {}
            tenant = meta.get("tenant_id")
            client_id = meta.get("client_id")
            secret_meta = meta.get("client_secret_meta") or {}
            secret_key_name = secret_meta.get("key") or "azure_client_secret"
            secret = store.retrieve_secret(secret_key_name)
            try:
                from azure.identity import ClientSecretCredential  # type: ignore
                cred = ClientSecretCredential(tenant, client_id, secret)
                cred.get_token("https://management.azure.com/.default")
                result["notice"] = "Azure credentials valid"
            except Exception as e:
                result.update({"ok": False, "fallback": True, "notice": f"Azure validation failed/skipped: {e}"})

        else:
            # provider 'none' or unknown -> no validation required
            result["notice"] = f"No provider selected ({provider}); running in noop mode"
            result["ok"] = True

    except Exception as e:
        result.update({"ok": False, "fallback": True, "notice": f"Unexpected validation error: {e}"})

    return result


def _validate_provider_async(cfg: dict, callback: t.Callable[[dict], None] | None = None) -> threading.Thread:
    """
    Spawn a background daemon thread to validate the provider.
    When finished, calls callback(result_dict) if callback provided.
    Returns the Thread object (already started).
    """
    def worker():
        res = validate_provider(cfg)
        if callback:
            try:
                callback(res)
            except Exception:
                # swallow callback errors - validation should never crash startup
                pass

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread
