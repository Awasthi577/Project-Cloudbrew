from __future__ import annotations

import json

from typer.testing import CliRunner

from LCF.cli import app, provisioning_pipeline


runner = CliRunner()


def _schema_required_ami():
    return {
        "block": {
            "attributes": {
                "ami": {"required": True, "type": "string"},
            }
        }
    }


def test_create_interactive_prompt_completion(monkeypatch) -> None:
    monkeypatch.setattr("LCF.cli.ensure_authenticated_for_resource", lambda *_: None)
    monkeypatch.setattr("LCF.provisioning.prompt_engine.typer.prompt", lambda *_, **__: "ami-123")

    monkeypatch.setattr(
        provisioning_pipeline,
        "_resolve_provider_and_type",
        lambda req: {
            "resource_type": req.resource_type,
            "provider": "opentofu",
            "provider_version": req.provider_version,
            "attributes": req.attributes,
            "identity": {"provider": "opentofu", "resource_type": req.resource_type, "logical_name": req.name},
        },
    )
    monkeypatch.setattr(
        provisioning_pipeline,
        "_load_provider_schema",
        lambda *_: {
            "provider_schema_key": "registry.opentofu.org/hashicorp/aws",
            "provider_version": "latest",
            "resource_schema": _schema_required_ami(),
        },
    )
    monkeypatch.setattr(
        provisioning_pipeline,
        "_run_tofu_stages",
        lambda *_args, **_kwargs: {"success": True, "steps": [], "workdir": "/tmp/cloudbrew"},
    )

    result = runner.invoke(app, ["create", "aws_instance", "vm-int", "--provider", "opentofu"])

    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["typed_config"]["ami"] == "ami-123"
    assert output["request"]["non_interactive"] is False


def test_create_non_interactive_fails_when_required_fields_missing(monkeypatch) -> None:
    monkeypatch.setattr("LCF.cli.ensure_authenticated_for_resource", lambda *_: None)
    monkeypatch.setattr(
        provisioning_pipeline,
        "_resolve_provider_and_type",
        lambda req: {
            "resource_type": req.resource_type,
            "provider": "opentofu",
            "provider_version": req.provider_version,
            "attributes": req.attributes,
            "identity": {"provider": "opentofu", "resource_type": req.resource_type, "logical_name": req.name},
        },
    )
    monkeypatch.setattr(
        provisioning_pipeline,
        "_load_provider_schema",
        lambda *_: {
            "provider_schema_key": "registry.opentofu.org/hashicorp/aws",
            "provider_version": "latest",
            "resource_schema": _schema_required_ami(),
        },
    )

    result = runner.invoke(app, ["create", "aws_instance", "vm-ni", "--provider", "opentofu", "--yes"])

    assert result.exit_code != 0
    assert "Missing required fields in non-interactive mode: ami" in result.output


def test_create_apply_flag_invokes_apply_path_only_with_yes(monkeypatch) -> None:
    monkeypatch.setattr("LCF.cli.ensure_authenticated_for_resource", lambda *_: None)

    monkeypatch.setattr(
        provisioning_pipeline,
        "_resolve_provider_and_type",
        lambda req: {
            "resource_type": req.resource_type,
            "provider": "opentofu",
            "provider_version": req.provider_version,
            "attributes": req.attributes,
            "identity": {"provider": "opentofu", "resource_type": req.resource_type, "logical_name": req.name},
        },
    )
    monkeypatch.setattr(
        provisioning_pipeline,
        "_load_provider_schema",
        lambda *_: {
            "provider_schema_key": "registry.opentofu.org/hashicorp/aws",
            "provider_version": "latest",
            "resource_schema": {"block": {"attributes": {}}},
        },
    )

    calls = []

    def fake_run_tofu(logical_name, hcl, run_apply):
        calls.append({"name": logical_name, "run_apply": run_apply, "hcl": hcl})
        return {"success": True, "steps": [], "workdir": "/tmp/cloudbrew"}

    monkeypatch.setattr(provisioning_pipeline, "_run_tofu_stages", fake_run_tofu)

    plan_only_result = runner.invoke(app, ["create", "aws_instance", "vm-plan", "--provider", "opentofu", "--apply"])
    apply_result = runner.invoke(
        app,
        ["create", "aws_instance", "vm-apply", "--provider", "opentofu", "--apply", "--yes"],
    )

    assert plan_only_result.exit_code == 0
    assert apply_result.exit_code == 0
    assert [call["run_apply"] for call in calls] == [False, True]
