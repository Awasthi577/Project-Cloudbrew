import json
from typer.testing import CliRunner
from LCF.cli import app
from pathlib import Path

runner = CliRunner()

def test_plan_no_spec_flags_errors():
    result = runner.invoke(app, ["plan", "--provider", "opentofu"])
    assert result.exit_code != 0
    # FIX: Updated "either" to "Either" to match actual Typer/Click output
    assert "Either --spec-file or --spec must be provided" in result.output

def test_apply_plan_missing_planid_errors():
    result = runner.invoke(app, ["apply-plan", "--provider", "opentofu"])
    # Typer should error because plan_id is required
    assert result.exit_code != 0