"""
Enhanced Schema-Driven Interactive Provisioning for CloudBrew.

This enhanced version automatically saves plans to files and prompts users
whether they want to apply the plan immediately, eliminating the need for
separate apply syntax.

Key Enhancements:
1. Automatic plan file creation and saving
2. Immediate apply prompt after plan creation
3. Clear plan file location display
4. Seamless integration with existing workflows
"""

from __future__ import annotations
import json
import os
import subprocess
import re
import sqlite3
from typing import Dict, Any, List, Tuple, Optional, Generator
from pathlib import Path

import typer
import click

# Local imports
from LCF.resource_resolver import ResourceResolver
from LCF.cloud_adapters.opentofu_adapter import OpenTofuAdapter


class EnhancedSchemaProvisioner:
    """
    Enhanced schema-driven provisioning engine with automatic plan saving and apply prompts.
    """
    
    def __init__(self):
        self.resolver = ResourceResolver()
        self.tofu_adapter = OpenTofuAdapter()
        self.schema_cache: Dict[str, Dict[str, Any]] = {}
    
    def _run_tofu_schema_command(self, command: List[str], timeout: int = 30) -> Tuple[int, str, str]:
        """Run a tofu schema command and return (returncode, stdout, stderr)."""
        try:
            proc = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout
            )
            return proc.returncode, proc.stdout, proc.stderr
        except FileNotFoundError:
            return 127, "", f"Binary not found: {command[0]}"
        except subprocess.TimeoutExpired:
            return 124, "", f"Command timed out after {timeout}s"
        except Exception as e:
            return 1, "", f"Error: {str(e)}"
    
    def _get_provider_schema(self, provider: str, resource_type: str) -> Optional[Dict[str, Any]]:
        """
        Extract schema for a specific resource type from OpenTofu provider.
        Returns None if schema cannot be obtained.
        """
        cache_key = f"{provider}:{resource_type}"
        if cache_key in self.schema_cache:
            return self.schema_cache[cache_key]
        
        # Try to get schema using tofu providers schema command
        try:
            # First, get all provider schemas
            rc, stdout, stderr = self._run_tofu_schema_command(["tofu", "providers", "schema", "-json"])
            if rc != 0:
                typer.echo(f"Warning: Could not get provider schemas: {stderr}")
                return None
            
            # Handle case where stdout is None or empty
            if not stdout:
                typer.echo(f"Warning: No schema data returned from tofu providers schema command")
                return None
            
            provider_schemas = json.loads(stdout)
            
            # Find the specific provider and resource
            for provider_name, provider_data in provider_schemas.get("provider_schemas", {}).items():
                if provider.lower() in provider_name.lower():
                    resource_schemas = provider_data.get("resource_schemas", {})
                    if resource_type in resource_schemas:
                        schema = resource_schemas[resource_type]
                        self.schema_cache[cache_key] = schema
                        return schema
            
            return None
            
        except (json.JSONDecodeError, KeyError, AttributeError) as e:
            typer.echo(f"Warning: Could not parse provider schema: {str(e)}")
            return None
        except Exception as e:
            typer.echo(f"Warning: Unexpected error getting provider schema: {str(e)}")
            return None
    
    def _extract_required_attributes(self, schema: Dict[str, Any]) -> List[str]:
        """
        Extract required attributes from a resource schema.
        """
        required_attrs = []
        
        # Look for required attributes in the schema
        if "block" in schema and "attributes" in schema["block"]:
            for attr_name, attr_schema in schema["block"]["attributes"].items():
                if attr_schema.get("required", False):
                    required_attrs.append(attr_name)
        
        # Look for required nested blocks
        if "block" in schema and "block_types" in schema["block"]:
            for block_name, block_schema in schema["block"]["block_types"].items():
                if block_schema.get("nesting_mode", "") == "single" and block_schema.get("min_items", 0) > 0:
                    required_attrs.append(block_name)
        
        return required_attrs
    
    def _extract_optional_attributes(self, schema: Dict[str, Any]) -> List[str]:
        """
        Extract optional attributes from a resource schema.
        """
        optional_attrs = []
        
        # Look for optional attributes in the schema
        if "block" in schema and "attributes" in schema["block"]:
            for attr_name, attr_schema in schema["block"]["attributes"].items():
                if not attr_schema.get("required", False) and not attr_schema.get("computed", False):
                    optional_attrs.append(attr_name)
        
        return optional_attrs
    
    def _get_attribute_description(self, schema: Dict[str, Any], attr_name: str) -> str:
        """
        Get a human-readable description for an attribute.
        """
        if "block" in schema and "attributes" in schema["block"]:
            attr_schema = schema["block"]["attributes"].get(attr_name, {})
            if "description" in attr_schema:
                return attr_schema["description"]
            elif "type" in attr_schema:
                return f"Type: {attr_schema['type']}"
        
        return ""
    
    def _is_computed_attribute(self, schema: Dict[str, Any], attr_name: str) -> bool:
        """
        Check if an attribute is computed (should not be prompted for).
        """
        if "block" in schema and "attributes" in schema["block"]:
            attr_schema = schema["block"]["attributes"].get(attr_name, {})
            return attr_schema.get("computed", False)
        return False
    
    def _prompt_for_attribute(self, attr_name: str, description: str, default_value: Optional[str] = None) -> str:
        """
        Interactive prompt for a single attribute value.
        """
        prompt = f"{attr_name}"
        if description:
            prompt += f" ({description})"
        if default_value is not None:
            prompt += f" [{default_value}]"
        prompt += ": "
        
        return typer.prompt(prompt, default=default_value)
    
    def _validate_attribute_value(self, attr_name: str, value: str, schema: Dict[str, Any]) -> bool:
        """
        Validate that a user-provided value is acceptable.
        Returns True if valid, False if invalid.
        """
        # For now, we do basic validation
        # In the future, we could add more sophisticated validation
        
        if not value.strip():
            typer.echo(f"Error: {attr_name} cannot be empty")
            return False
        
        # Check if this is a sensitive field that might contain cloud values we shouldn't guess
        sensitive_fields = ["ami", "image_id", "subnet_id", "zone", "region", "machine_type", "instance_type"]
        if any(field in attr_name.lower() for field in sensitive_fields):
            # For sensitive fields, we need to ensure the user is providing a real value
            # We can't validate it's correct, but we can ensure it's not a placeholder
            placeholder_patterns = ["placeholder", "example", "replace_me", "your_", "<.*>"]
            if any(re.search(pattern, value, re.IGNORECASE) for pattern in placeholder_patterns):
                typer.echo(f"Error: {attr_name} appears to be a placeholder value. Please provide the actual value.")
                return False
        
        return True
    
    def _gather_resource_spec_interactively(self, provider: str, resource_type: str) -> Dict[str, Any]:
        """
        Interactive workflow to gather all required attributes for a resource.
        """
        spec: Dict[str, Any] = {
            "provider": provider,
            "type": resource_type
        }
        
        # Get the schema for this resource
        schema = self._get_provider_schema(provider, resource_type)
        if not schema:
            typer.echo(f"Warning: Could not retrieve schema for {provider}:{resource_type}")
            typer.echo("Falling back to basic provisioning with common AWS instance attributes.")
            
            # Provide a basic fallback for AWS instances
            if provider == "aws" and "instance" in resource_type.lower():
                # Add common AWS instance attributes
                spec["ami"] = typer.prompt("AMI ID (e.g., ami-0abcdef1234567890)", default="ami-0a63f6f9f6f9abcde")
                spec["instance_type"] = typer.prompt("Instance type", default="t3.micro")
                spec["region"] = typer.prompt("AWS region", default="us-east-1")
                
                # Add tags
                tags = {}
                name_tag = typer.prompt("Name tag for the instance", default=f"{resource_type}-instance")
                if name_tag:
                    tags["Name"] = name_tag
                tags["ManagedBy"] = "CloudBrew"
                spec["tags"] = tags
                
                return spec
            else:
                typer.echo("Falling back to minimal provisioning. Some required fields may be missing.")
                return spec
        
        # Extract required and optional attributes
        required_attrs = self._extract_required_attributes(schema)
        optional_attrs = self._extract_optional_attributes(schema)
        
        typer.echo(f"\n=== Creating {resource_type} with {provider} ===")
        typer.echo(f"Provider: {provider}")
        typer.echo(f"Resource Type: {resource_type}")
        
        # Prompt for required attributes
        if required_attrs:
            typer.echo(f"\nRequired attributes:")
            for attr_name in required_attrs:
                if self._is_computed_attribute(schema, attr_name):
                    continue  # Skip computed attributes
                
                description = self._get_attribute_description(schema, attr_name)
                
                while True:
                    value = self._prompt_for_attribute(attr_name, description)
                    if self._validate_attribute_value(attr_name, value, schema):
                        spec[attr_name] = value
                        break
        
        # Prompt for optional attributes (with defaults where possible)
        if optional_attrs:
            typer.echo(f"\nOptional attributes:")
            for attr_name in optional_attrs:
                description = self._get_attribute_description(schema, attr_name)
                
                # For optional attributes, we can provide reasonable defaults
                default_value = None
                if "name" in attr_name.lower():
                    default_value = f"{resource_type}-instance"
                elif "tags" in attr_name.lower():
                    default_value = "CloudBrew-Managed"
                
                value = self._prompt_for_attribute(attr_name, description, default_value)
                if value:  # Only add if user provided a value
                    spec[attr_name] = value
        
        return spec
    
    def _show_confirmation(self, provider: str, resource_type: str, spec: Dict[str, Any]) -> bool:
        """
        Show the final configuration and ask for user confirmation.
        """
        typer.echo("\n=== Configuration Summary ===")
        typer.echo(f"Provider: {provider}")
        typer.echo(f"Resource Type: {resource_type}")
        typer.echo("Configuration:")
        
        for key, value in spec.items():
            if key not in ["provider", "type"]:  # Skip metadata
                typer.echo(f"  {key}: {value}")
        
        confirm = typer.confirm("\nProceed with creation?")
        return confirm
    
    def create_resource_with_auto_plan(self, resource_name: str, provider_hint: Optional[str] = None) -> Dict[str, Any]:
        """
        Enhanced entry point that automatically creates plans and prompts for immediate application.
        This eliminates the need for separate apply syntax.
        """
        # Resolve the resource type and provider
        try:
            resolution = self.resolver.resolve(resource=resource_name, provider=provider_hint or "auto")
            
            if "_resolved" not in resolution or "_provider" not in resolution:
                typer.echo(f"Error: Could not resolve resource '{resource_name}'")
                typer.echo(f"Available providers: {resolution.get('tried_providers', [])}")
                typer.echo(f"Top candidates: {resolution.get('top_candidates', [])}")
                return {"success": False, "error": "Resource resolution failed"}
            
            resolved_provider = resolution["_provider"]
            resolved_resource_type = resolution["_resolved"]
            
            typer.echo(f"Resolved: {resource_name} -> {resolved_provider}:{resolved_resource_type}")
            
            # Gather specification interactively
            spec = self._gather_resource_spec_interactively(resolved_provider, resolved_resource_type)
            
            # Show confirmation
            if not self._show_confirmation(resolved_provider, resolved_resource_type, spec):
                typer.echo("Creation cancelled by user.")
                return {"success": False, "error": "User cancelled"}
            
            # Create a plan first and save it to a file
            typer.echo("\n=== Creating and Saving Plan ===")
            plan_result = self.tofu_adapter.create_instance(resource_name, spec, plan_only=True)
            
            if not plan_result.get("success", False):
                typer.echo(f"❌ Failed to create plan: {plan_result.get('error', 'Unknown error')}")
                return {"success": False, "error": plan_result.get("error", "Unknown error")}
            
            # Extract or determine the plan file path
            plan_id = plan_result.get("plan_id")
            if not plan_id:
                # If no plan_id in result, construct it based on the working directory
                plan_id = f".cloudbrew_tofu/{resource_name}/plan.tfplan"
            
            typer.echo(f"✅ Plan created and saved successfully!")
            typer.echo(f"📁 Plan File: {plan_id}")
            
            # Show the plan content for user review
            typer.echo("\n=== Plan Content ===")
            typer.echo("Resource Configuration:")
            typer.echo(f"  Name: {resource_name}")
            typer.echo(f"  Type: {resolved_resource_type}")
            typer.echo(f"  Provider: {resolved_provider}")
            
            for key, value in spec.items():
                if key not in ["provider", "type", "name"]:
                    typer.echo(f"  {key}: {value}")
            
            # Prompt user whether to apply immediately
            typer.echo("\n" + "="*50)
            apply_now = typer.confirm("🚀 Apply this plan to cloud now?")
            
            if apply_now:
                # Apply the plan immediately
                typer.echo("\n=== Applying Plan to Cloud ===")
                apply_result = self.tofu_adapter.apply_plan(plan_id)
                
                if apply_result.get("success", False):
                    typer.echo(f"✅ Successfully applied plan and created {resource_name} in the cloud!")
                    typer.echo(f"🎉 Resource '{resource_name}' is now running")
                    return {"success": True, "result": apply_result, "plan_id": plan_id, "status": "applied"}
                else:
                    typer.echo(f"❌ Failed to apply plan: {apply_result.get('error', 'Unknown error')}")
                    typer.echo("⚠️  The plan file has been saved for later use")
                    return {"success": False, "error": apply_result.get("error", "Unknown error"), "plan_id": plan_id, "status": "plan_saved"}
            else:
                # Just return the plan information with clear instructions
                typer.echo("\n⏸️  Plan saved but not applied to cloud.")
                typer.echo(f"📝 Plan file location: {plan_id}")
                typer.echo("\nTo apply this plan later, use:")
                typer.echo(f"   cloudbrew apply-plan --provider {resolved_provider} --plan-id '{plan_id}'")
                typer.echo("\nOr to destroy the plan without applying:")
                typer.echo(f"   rm '{plan_id}'")
                return {"success": True, "result": plan_result, "plan_id": plan_id, "status": "plan_saved"}
                
        except Exception as e:
            typer.echo(f"❌ Error during interactive provisioning: {str(e)}")
            return {"success": False, "error": str(e)}


def enhanced_create_command(
    resource_name: str = typer.Argument(..., help="Resource name or type (e.g., aws_instance, myvm)"),
    provider: Optional[str] = typer.Option(None, help="Provider hint (aws, gcp, azure, etc.)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-confirm (skip interactive prompts)"),
):
    """
    Enhanced schema-driven interactive resource creation with automatic plan saving and apply prompts.
    
    This command:
    1. Extracts provider schemas from OpenTofu
    2. Prompts for required attributes interactively
    3. Creates and saves a plan file automatically
    4. Prompts whether to apply the plan immediately
    5. Eliminates need for separate apply syntax
    
    Example:
        cloudbrew create aws_instance myvm
        cloudbrew create aws_instance myvm --provider aws
    """
    provisioner = EnhancedSchemaProvisioner()
    
    if yes:
        # Non-interactive mode
        typer.echo("Non-interactive mode not yet implemented for enhanced provisioning")
        typer.echo("Falling back to basic provisioning")
        return
    
    result = provisioner.create_resource_with_auto_plan(resource_name, provider)
    
    # Output result in JSON format for programmatic use
    typer.echo("\n" + "="*50)
    typer.echo("📋 Operation Summary:")
    typer.echo(json.dumps(result, indent=2))


# CLI Integration
enhanced_app = typer.Typer()
enhanced_app.command("create")(enhanced_create_command)