# tests/cli/test_destroy_vm.py
import json
import os
import pytest
from typer.testing import CliRunner

from LCF import cli

runner = CliRunner()


def test_destroy_vm_terraform(monkeypatch, tmp_path):
    # Patch TerraformAdapter.destroy_instance to return True
    called = {}

    class DummyAdapter:
        def __init__(self, db_path=None): pass
        def destroy_instance(self, adapter_id: str) -> bool:
            called["adapter_id"] = adapter_id
            return True

    monkeypatch.setattr(cli, "TerraformAdapter", DummyAdapter)

    result = runner.invoke(cli.app, ["destroy-vm", "vm1", "--provider", "terraform"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["destroyed"] is True
    assert data["name"] == "vm1"
    assert called["adapter_id"] == "terraform-vm1"


def test_destroy_vm_pulumi(monkeypatch):
    lines = []

    def fake_destroy(stack):
        lines.append(f"destroy:{stack}")
        yield "Destroy completed."

    monkeypatch.setattr(cli.pulumi_adapter, "destroy", fake_destroy)

    result = runner.invoke(cli.app, ["destroy-vm", "stack1", "--provider", "pulumi"])
    assert result.exit_code == 0
    assert "Destroy completed." in result.stdout
    assert lines and lines[0] == "destroy:stack1"
