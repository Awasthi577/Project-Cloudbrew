from __future__ import annotations

from pathlib import Path

from LCF.provisioning.pipeline import ProvisioningPipeline


def test_pipeline_persists_run_metadata_and_replay(tmp_path) -> None:
    pipeline = ProvisioningPipeline(run_store_path=str(tmp_path / "runs.sqlite"))

    pipeline._resolve_provider_and_type = lambda req: {  # type: ignore[assignment]
        "resource_type": req.resource_type,
        "provider": "opentofu",
        "provider_version": req.provider_version,
        "attributes": req.attributes,
        "identity": {"provider": "opentofu", "resource_type": req.resource_type, "logical_name": req.name},
    }
    pipeline._load_provider_schema = lambda *_: {  # type: ignore[assignment]
        "provider_schema_key": "registry.opentofu.org/hashicorp/aws",
        "provider_version": "latest",
        "resource_schema": {"block": {"attributes": {"ami": {"required": True, "type": "string"}}}},
    }
    pipeline._collect_missing_values = lambda **kwargs: (  # type: ignore[assignment]
        {"ami": "ami-123"},
        {"missing_required_fields": [], "missing_required_blocks": [], "ok": True},
    )
    pipeline._run_tofu_stages = lambda *_args, **_kwargs: {  # type: ignore[assignment]
        "success": True,
        "steps": [{"command": "tofu plan -no-color", "returncode": 0}],
        "workdir": str(tmp_path / "work"),
    }

    result = pipeline.execute(
        {
            "name": "vm-a",
            "resource_type": "aws_instance",
            "provider": "opentofu",
            "attributes": {},
            "plan_only": True,
            "non_interactive": False,
        }
    )

    assert result["run_id"].startswith("run-")
    assert result["prompted_fields"] == ["ami"]
    assert result["hcl_artifact_path"].endswith("main.tf")

    stored = pipeline.get_run(result["run_id"])
    assert stored is not None
    assert stored["resolved_identity"]["resource_type"] == "aws_instance"
    assert stored["final_spec"]["ami"] == "ami-123"

    replay = pipeline.replay(result["run_id"])
    assert replay["run_id"] == result["run_id"]


def test_pipeline_run_store_json_backend(tmp_path) -> None:
    pipeline = ProvisioningPipeline(run_store_path=str(tmp_path / "runs.json"))
    run_id = pipeline.run_store.save({"success": True}, run_id="run-fixed")
    assert run_id == "run-fixed"
    assert Path(tmp_path / "runs.json").exists()
    assert pipeline.get_run("run-fixed") is not None
