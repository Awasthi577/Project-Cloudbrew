# LCF/auth_utils.py
"""
Authentication utilities for CloudBrew.
Handles checking if users are authenticated for specific cloud providers.
"""

from __future__ import annotations
import json
import os
import pathlib
import typing as t
from typing import Optional, Dict, Any

import typer

CONFIG_DIR = pathlib.Path.home() / ".cloudbrew"
CONFIG_PATH = CONFIG_DIR / "config.json"


def _load_config() -> Optional[dict]:
    """Load CloudBrew configuration from file."""
    if not CONFIG_PATH.exists():
        return None
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return None


def is_authenticated_for_provider(provider: str) -> bool:
    """
    Check if user is authenticated for a specific cloud provider.
    
    Args:
        provider: Cloud provider name (aws, gcp, azure)
        
    Returns:
        True if authenticated, False otherwise
    """
    config = _load_config()
    if not config:
        return False
    
    creds = config.get("creds", {})
    
    if provider == "aws":
        return bool(creds.get("aws"))
    elif provider == "gcp":
        return bool(creds.get("gcp"))
    elif provider == "azure":
        return bool(creds.get("azure"))
    else:
        return False


def get_authenticated_providers() -> list[str]:
    """
    Get list of providers for which user is authenticated.
    
    Returns:
        List of provider names (aws, gcp, azure)
    """
    config = _load_config()
    if not config:
        return []
    
    creds = config.get("creds", {})
    providers = []
    
    if creds.get("aws"):
        providers.append("aws")
    if creds.get("gcp"):
        providers.append("gcp")
    if creds.get("azure"):
        providers.append("azure")
    
    return providers


def check_authentication_or_die(provider: str, resource_type: str) -> None:
    """
    Check if user is authenticated for the specified provider.
    If not authenticated, display error message and exit.
    
    Args:
        provider: Cloud provider name (aws, gcp, azure)
        resource_type: Type of resource being created
        
    Raises:
        typer.Exit: If user is not authenticated
    """
    if not is_authenticated_for_provider(provider):
        typer.secho(
            f"ERROR: Not authenticated for {provider.upper()} provider", 
            fg=typer.colors.RED, 
            bold=True
        )
        typer.echo(
            f"You must run 'cloudbrew init' and configure {provider.upper()} credentials "
            f"before creating {resource_type} resources."
        )
        typer.echo("Run: cloudbrew init")
        raise typer.Exit(code=1)


def get_default_provider() -> Optional[str]:
    """
    Get the default provider from configuration.
    
    Returns:
        Default provider name or None
    """
    config = _load_config()
    if not config:
        return None
    return config.get("default_provider")


def ensure_authenticated_for_resource(provider: str, resource_type: str) -> None:
    """
    Ensure user is authenticated for creating a specific resource type.
    
    Args:
        provider: Cloud provider name
        resource_type: Type of resource being created
        
    Raises:
        typer.Exit: If user is not authenticated
    """
    # Check if provider is 'noop' (no authentication needed)
    if provider == "noop":
        return
    
    # Check if user is authenticated for this provider
    if not is_authenticated_for_provider(provider):
        typer.secho(
            f"ERROR: Cannot create {resource_type} - Not authenticated for {provider.upper()}", 
            fg=typer.colors.RED, 
            bold=True
        )
        typer.echo(
            f"Please run 'cloudbrew init' and configure {provider.upper()} credentials first."
        )
        raise typer.Exit(code=1)