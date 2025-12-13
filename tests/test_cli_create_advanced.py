import json
import os
from typer.testing import CliRunner
from unittest.mock import MagicMock, patch
from LCF.cli import app

runner = CliRunner()

# ------------------------------------------------------------------------
# Fixtures / Mocks
# ------------------------------------------------------------------------

@patch("LCF.cli.ResourceResolver")
@patch("LCF.cli.AutoscalerManager")
def test_create_positional_and_dynamic_flags(mock_mgr_cls, mock_rr_cls):
    """
    Test: cloudbrew create aws_quantum_computer MyQPU --qubit_count 50
    """
    # 1. Mock Resolver to return success
    mock_rr = mock_rr_cls.return_value
    mock_rr.resolve.return_value = {
        "_resolved": "aws_quantum_computer",
        "_provider": "aws"
    }

    # 2. Mock Manager run_once
    mock_mgr = mock_mgr_cls.return_value
    mock_mgr.run_once.return_value = {"status": "success", "id": "MyQPU"}

    # 3. Run Command
    result = runner.invoke(app, [
        "create",
        "aws_quantum_computer", "MyQPU",  # Positional
        "--qubit_count", "50",            # Dynamic Flag
        "--cooling", "cryogenic"          # Another Dynamic Flag
    ])

    # 4. Assertions
    assert result.exit_code == 0
    
    # Verify Resolver was called correctly
    mock_rr.resolve.assert_called_with(resource="aws_quantum_computer", provider="auto")
    
    # Verify Manager received the merged spec
    call_args = mock_mgr.run_once.call_args
    # call_args[0] are positional args passed to run_once(name, spec, ...)
    # spec is at index 1
    passed_name = call_args[0][0]
    passed_spec = call_args[0][1]

    assert passed_name == "MyQPU"
    assert passed_spec["type"] == "aws_quantum_computer"
    assert passed_spec["qubit_count"] == 50  # Integers parsed correctly
    assert passed_spec["cooling"] == "cryogenic"


@patch("LCF.cli.ResourceResolver")
def test_create_validation_failure(mock_rr_cls):
    """
    Test validation error when resource doesn't exist.
    """
    mock_rr = mock_rr_cls.return_value
    mock_rr.resolve.return_value = {
        "message": "No clear mapping for 'aws_unicorn'",
        "top_candidates": [{"name": "aws_ec2", "score": 0.5}],
        "mode": "dynamic_lookup_failed"
    }

    result = runner.invoke(app, ["create", "aws_unicorn", "MyBeast"])

    assert result.exit_code == 1
    assert "Validation Error" in result.stdout
    assert "Did you mean" in result.stdout
    assert "aws_ec2" in result.stdout


@patch("LCF.cli.ResourceResolver")
@patch("LCF.cli.AutoscalerManager")
def test_create_with_extra_file(mock_mgr_cls, mock_rr_cls, tmp_path):
    """
    Test: cloudbrew create ... --extra config.json
    """
    # Setup Resolver
    mock_rr_cls.return_value.resolve.return_value = {"_resolved": "aws_s3_bucket"}
    
    # FIX: Set a return value for run_once so json.dumps doesn't crash
    mock_mgr_cls.return_value.run_once.return_value = {"status": "success", "id": "MyBucket"}

    # Create a temporary extra config file
    extra_file = tmp_path / "extra.json"
    extra_file.write_text(json.dumps({
        "versioning": {"enabled": True},
        "tags": {"Environment": "Dev"}
    }))

    result = runner.invoke(app, [
        "create", "aws_s3_bucket", "MyBucket",
        "--extra", str(extra_file),
        "--region", "us-west-2" # Override/Append via CLI
    ])

    # Debugging output if it fails again
    if result.exit_code != 0:
        print(result.stdout)
        print(result.exception)

    assert result.exit_code == 0
    
    # Check that extra file was merged
    passed_spec = mock_mgr_cls.return_value.run_once.call_args[0][1]
    
    assert passed_spec["versioning"]["enabled"] is True
    assert passed_spec["tags"]["Environment"] == "Dev"
    assert passed_spec["region"] == "us-west-2"

def test_create_missing_args():
    """
    Test that it fails cleanly if no type/name provided.
    """
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 1
    assert "Error: You must provide a Resource Type and Name" in result.stdout