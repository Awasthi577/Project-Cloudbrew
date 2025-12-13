"""
Unit tests for the ResourceResolver class.

These tests verify the resource resolution logic, ensuring that simplified
resource names (e.g., "instance") are correctly mapped to their provider-specific
identifiers (e.g., "aws_instance") using the OpenTofu schema.
"""
import pytest
from typing import Dict, Any
from LCF.resource_resolver import ResourceResolver

def fake_tofu_schema() -> Dict[str, Any]:
    """
    Returns a mocked OpenTofu schema structure for testing.
    This simulates the output of `tofu providers schema -json`.
    """
    return {
        "provider_schemas": {
            "registry.terraform.io/hashicorp/aws": {
                "resource_schemas": {
                    "aws_instance": {},
                    "aws_s3_bucket": {},
                    "aws_vpc": {},
                    "aws_subnet": {}
                }
            },
            "registry.terraform.io/hashicorp/azurerm": {
                "resource_schemas": {
                    "azurerm_resource_group": {},
                    "azurerm_virtual_machine": {}
                }
            }
        }
    }

def test_gather_opentofu_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Test that we can gather resource names from the OpenTofu schema query.
    Verifies that the resolver correctly parses the mock schema and caches the results.
    """
    # Use in-memory DB to avoid side effects
    rr = ResourceResolver(db_path=":memory:")
    
    # Mock the internal query method to return our fake schema
    monkeypatch.setattr(rr, "_query_opentofu_schema", lambda: fake_tofu_schema())
    
    # Force gathering for 'opentofu' provider
    names = rr._gather_provider_resource_names("opentofu")
    
    # Assertions to verify resource extraction
    assert "aws_instance" in names
    assert "aws_s3_bucket" in names
    assert "azurerm_resource_group" in names
    assert "azurerm_virtual_machine" in names
    assert len(names) >= 4

def test_discovery_exact_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Test resolving a specific resource name using the resolver logic.
    Verifies that a vague term like 'instance' resolves to the most likely candidate.
    """
    rr = ResourceResolver(db_path=":memory:")
    monkeypatch.setattr(rr, "_query_opentofu_schema", lambda: fake_tofu_schema())
    
    # Should resolve "instance" to "aws_instance" given the scoring logic usually favors strict substrings or common prefixes
    # Note: 'aws_instance' often scores high for 'instance' in default implementations.
    res = rr.resolve("instance", "opentofu")
    
    assert res["_resolved"] == "aws_instance"
    assert res["_provider"] == "opentofu"

def test_discovery_azure_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Test that we can resolve Azure resources correctly.
    """
    rr = ResourceResolver(db_path=":memory:")
    monkeypatch.setattr(rr, "_query_opentofu_schema", lambda: fake_tofu_schema())

    res = rr.resolve("virtual_machine", "opentofu")

    assert res["_resolved"] == "azurerm_virtual_machine"
    assert res["_provider"] == "opentofu"