from __future__ import annotations

import json

from typer.testing import CliRunner

from LCF.cli import app, provisioning_pipeline


runner = CliRunner()


def test_runs_show_command(monkeypatch) -> None:
    monkeypatch.setattr(
        provisioning_pipeline,
        "get_run",
        lambda run_id: {"run_id": run_id, "resolved_identity": {"provider": "opentofu"}},
    )

    result = runner.invoke(app, ["runs", "show", "run-abc"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["run_id"] == "run-abc"


def test_runs_replay_command(monkeypatch) -> None:
    monkeypatch.setattr(
        provisioning_pipeline,
        "replay",
        lambda run_id: {"run_id": run_id, "success": True, "tofu": {"steps": []}},
    )

    result = runner.invoke(app, ["runs", "replay", "run-abc"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["run_id"] == "run-abc"
