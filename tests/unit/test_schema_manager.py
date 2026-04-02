import json
from unittest import mock

from LCF.cloud_adapters.schema_manager import SchemaManager


def test_schema_manager_creates_workdir_and_skips_without_binary(tmp_path):
    workdir = tmp_path / "tofu-workdir"
    manager = SchemaManager(work_dir=str(workdir), tofu_bin="")

    assert workdir.exists()
    assert manager.get("aws_instance") == {"kind": "block", "blocks": {}, "attributes": {}}


@mock.patch("LCF.cloud_adapters.schema_manager.subprocess.run")
def test_schema_manager_uses_configured_binary(mock_run, tmp_path):
    workdir = tmp_path / "tofu-workdir"

    # Return an empty valid schema response from tofu providers schema -json
    mock_run.side_effect = [
        mock.Mock(returncode=0, stdout="", stderr=""),
        mock.Mock(returncode=0, stdout='{"provider_schemas": {}}', stderr=""),
    ]

    SchemaManager(work_dir=str(workdir), tofu_bin="/custom/tofu")

    assert mock_run.call_args_list[0].args[0][0] == "/custom/tofu"
    assert mock_run.call_args_list[1].args[0][0] == "/custom/tofu"


def test_schema_manager_parses_required_and_constraints(tmp_path):
    workdir = tmp_path / "tofu-workdir"
    workdir.mkdir()
    (workdir / ".terraform").mkdir()

    schema_payload = {
        "provider_schemas": {
            "registry.opentofu.org/hashicorp/aws": {
                "resource_schemas": {
                    "aws_instance": {
                        "block": {
                            "attributes": {
                                "ami": {"required": True, "type": "string"},
                                "arn": {"computed": True, "type": "string"},
                            },
                            "block_types": {
                                "root_block_device": {
                                    "nesting_mode": "list",
                                    "min_items": 1,
                                    "max_items": 1,
                                    "block": {
                                        "attributes": {
                                            "volume_type": {
                                                "required": True,
                                                "type": ["object", {"kind": "string"}],
                                            }
                                        }
                                    },
                                }
                            },
                        }
                    }
                }
            }
        }
    }
    with mock.patch("LCF.cloud_adapters.schema_manager.subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout=json.dumps(schema_payload),
            stderr="",
        )
        manager = SchemaManager(work_dir=str(workdir), tofu_bin="/custom/tofu")

    resource = manager.get_resource_schema("aws_instance")
    assert resource["attributes"]["ami"]["required"] is True
    assert resource["blocks"]["root_block_device"]["min_items"] == 1
    assert manager.list_required_paths("aws_instance") == [
        "ami",
        "root_block_device",
        "root_block_device.volume_type",
    ]

    rules = manager.list_constraint_rules("aws_instance")
    assert any(r["path"] == "ami" and r["kind"] == "attribute" for r in rules)
    assert any(
        r["path"] == "root_block_device" and r["kind"] == "block" and r["min_items"] == 1
        for r in rules
    )


def test_schema_manager_cache_key_changes_with_lockfile(tmp_path):
    workdir = tmp_path / "tofu-workdir"
    workdir.mkdir()

    (workdir / ".terraform.lock.hcl").write_text(
        'provider "registry.opentofu.org/hashicorp/aws" {\n  version = "5.0.0"\n}\n',
        encoding="utf-8",
    )
    first = SchemaManager(work_dir=str(workdir), tofu_bin="")

    (workdir / ".terraform.lock.hcl").write_text(
        'provider "registry.opentofu.org/hashicorp/aws" {\n  version = "5.1.0"\n}\n',
        encoding="utf-8",
    )
    second = SchemaManager(work_dir=str(workdir), tofu_bin="")

    assert first.cache_key != second.cache_key
