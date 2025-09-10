# tests/cli/test_plan_errors.py
import json
from typer.testing import CliRunner
from LCF.cli import app
from pathlib import Path
runner = CliRunner()

def test_plan_no_spec_flags_errors():
    result = runner.invoke(app, ["plan", "--provider", "terraform"])
    assert result.exit_code != 0
    assert "either --spec-file or --spec must be provided" in result.output

def test_apply_plan_missing_planid_errors():
    result = runner.invoke(app, ["apply-plan", "--provider", "terraform"])
    # Typer should error because plan_id is required
    assert result.exit_code != 0
