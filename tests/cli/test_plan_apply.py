# tests/cli/test_plan_apply.py
import json
from typer.testing import CliRunner
from unittest import mock
from LCF.cli import app
from LCF.offload.manager import OffloadManager

runner = CliRunner()

def test_plan_terraform_returns_plan_id(tmp_path, monkeypatch):
    # Arrange: mock TerraformAdapter.create_instance to return a plan_id
    fake_plan = str(tmp_path / "fake.plan")
    class FakeTA:
        def __init__(self, db_path=None):
            pass
        def create_instance(self, logical_id, spec, plan_only=True):
            return {"plan_id": fake_plan, "diff": "ok"}

    monkeypatch.setattr("LCF.cli.TerraformAdapter", FakeTA)

    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps({"name": "demo", "provider": "aws"}), encoding="utf-8")

    # Act
    result = runner.invoke(app, ["plan", "--provider", "terraform", "--spec-file", str(spec_file)])

    # Assert
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert "plan_id" in out
    assert out["plan_id"] == fake_plan

def test_apply_plan_sync_calls_apply_plan(monkeypatch, tmp_path):
    # Arrange: mock TerraformAdapter.apply_plan
    class FakeTA:
        def apply_plan(self, plan_id):
            return {"success": True, "output": "applied"}

    monkeypatch.setattr("LCF.cli.TerraformAdapter", FakeTA)

    fake_plan = str(tmp_path / "fake.plan")
    # Act
    result = runner.invoke(app, ["apply-plan", "--provider", "terraform", "--plan-id", fake_plan])

    # Assert
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out.get("success") is True

def test_apply_plan_async_enqueues(monkeypatch, tmp_path):
    # Arrange: monkeypatch OffloadManager.enqueue to assert called and return id
    captured = {}
    class FakeOff:
        def __init__(self, db_path=None):
            pass
        def enqueue(self, adapter, task_type, payload=None):
            captured['adapter'] = adapter
            captured['task_type'] = task_type
            captured['payload'] = payload
            return 123

    monkeypatch.setattr("LCF.cli.OffloadManager", FakeOff)

    fake_plan = str(tmp_path / "fake.plan")
    # Act
    result = runner.invoke(app, ["apply-plan", "--provider", "terraform", "--plan-id", fake_plan, "--async"])

    # Assert
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out.get("enqueued_task_id") == 123
    assert captured["adapter"] == "terraform"
    assert captured["task_type"] == "apply_plan"
    assert captured["payload"]["plan_id"] == fake_plan
