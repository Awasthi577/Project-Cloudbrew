from __future__ import annotations

from LCF.provisioning.prompt_engine import NonInteractivePromptAdapter, PromptEngine


def _deep_schema():
    return {
        "block": {
            "attributes": {
                "name": {"required": True, "type": "string"},
            },
            "block_types": {
                "network_interface": {
                    "nesting_mode": "list",
                    "min_items": 1,
                    "block": {
                        "attributes": {
                            "subnet_id": {"required": True, "type": "string"},
                        },
                        "block_types": {
                            "access_config": {
                                "nesting_mode": "list",
                                "min_items": 1,
                                "block": {
                                    "attributes": {
                                        "nat_ip": {"required": True, "type": "string"},
                                    }
                                },
                            }
                        },
                    },
                }
            },
        }
    }


def test_missing_path_detection_walks_nested_block_levels() -> None:
    result = PromptEngine(NonInteractivePromptAdapter(strict=True)).build_canonical_spec(
        schema=_deep_schema(),
        current_spec={"name": "vm-nested"},
    )

    assert sorted(result.missing_required_paths) == [
        "network_interface.access_config.nat_ip",
        "network_interface.subnet_id",
    ]


def test_missing_path_detection_resolves_when_values_present() -> None:
    result = PromptEngine(NonInteractivePromptAdapter(strict=True)).build_canonical_spec(
        schema=_deep_schema(),
        current_spec={
            "name": "vm-nested",
            "network_interface": [
                {
                    "subnet_id": "subnet-123",
                    "access_config": [{"nat_ip": "34.1.2.3"}],
                }
            ],
        },
    )

    assert result.missing_required_paths == []
