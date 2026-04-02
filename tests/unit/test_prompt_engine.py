from __future__ import annotations

from typing import Any, Dict, List, Tuple

from LCF.provisioning.prompt_engine import (
    NonInteractivePromptAdapter,
    PromptContext,
    PromptEngine,
)


class StaticPromptAdapter:
    def __init__(self, values: Dict[str, Any]) -> None:
        self.values = values
        self.calls: List[str] = []

    def resolve_value(self, ctx: PromptContext) -> Tuple[bool, Any]:
        self.calls.append(ctx.path)
        if ctx.path in self.values:
            return True, self.values[ctx.path]
        return False, None


def _schema_with_nested_required() -> Dict[str, Any]:
    return {
        "block": {
            "attributes": {
                "name": {"required": True, "type": "string"},
                "tags": {"optional": True, "type": "map"},
            },
            "block_types": {
                "network_interface": {
                    "nesting_mode": "list",
                    "min_items": 1,
                    "block": {
                        "attributes": {
                            "subnet_id": {"required": True, "type": "string"},
                            "security_group": {"optional": True, "type": "string"},
                        }
                    },
                }
            },
        }
    }


def test_prompt_engine_collects_required_recursively() -> None:
    adapter = StaticPromptAdapter(
        {
            "name": "vm-1",
            "network_interface": [{"subnet_id": "subnet-123"}],
        }
    )
    result = PromptEngine(adapter).build_canonical_spec(_schema_with_nested_required(), current_spec={})

    assert result.missing_required_paths == []
    assert result.canonical_spec["name"] == "vm-1"
    assert result.canonical_spec["network_interface"][0]["subnet_id"] == "subnet-123"


def test_prompt_engine_optional_prompts_only_in_advanced_mode() -> None:
    adapter = StaticPromptAdapter(
        {
            "name": "vm-1",
            "network_interface": [{"subnet_id": "subnet-123"}],
            "tags": {"env": "dev"},
            "network_interface.security_group": "sg-1",
        }
    )
    result = PromptEngine(adapter).build_canonical_spec(
        _schema_with_nested_required(),
        current_spec={},
        prompt_advanced=True,
    )

    assert result.missing_required_paths == []
    assert "tags" in result.canonical_spec
    # nested optional path is attempted during advanced prompt phase
    assert "network_interface.security_group" in adapter.calls


def test_non_interactive_adapter_strict_missing_required() -> None:
    result = PromptEngine(NonInteractivePromptAdapter(strict=True)).build_canonical_spec(
        _schema_with_nested_required(),
        current_spec={},
    )

    assert "name" in result.missing_required_paths
    assert "network_interface.subnet_id" in result.missing_required_paths
