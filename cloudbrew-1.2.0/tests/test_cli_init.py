# tests/test_cli_init.py
import json
import sys
import types
import pathlib

import pytest
from typer.testing import CliRunner

# Import the Typer app and CONFIG_PATH from the package module.
# This assumes your package is importable as `LCF` (adjust if your layout differs).
from LCF.cli import app       # top-level app (mounts subcommands)
from LCF.cli_init import CONFIG_PATH


runner = CliRunner()


def _make_fake_boto3_module():
    """Create a fake boto3 module with a client(...) that supports get_caller_identity()."""
    mod = types.ModuleType("boto3")

    class FakeSTS:
        def get_caller_identity(self):
            return {"UserId": "FAKE-USER"}

    def client(service_name, *args, **kwargs):
        if service_name == "sts":
            return FakeSTS()
        raise RuntimeError(f"Unexpected service: {service_name}")

    mod.client = client
    return mod


def test_init_noninteractive(tmp_path, monkeypatch):
    """
    Running `cloudbrew init --yes` should create a config file with default_provider=none.
    We isolate HOME to tmp_path so the test does not touch the real user home.
    """
    # Isolate HOME so CONFIG_DIR is under tmp_path
    monkeypatch.setenv("HOME", str(tmp_path))

    result = runner.invoke(app, ["init", "--yes"])
    assert result.exit_code == 0

    # CONFIG_PATH should be created under the isolated HOME
    assert CONFIG_PATH.exists()
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert cfg["default_provider"] == "none"
    assert cfg["creds"] == {}


def test_init_aws_flow(tmp_path, monkeypatch):
    """
    Simulate interactive AWS flow. We:
      - isolate HOME to tmp_path
      - provide interactive inputs for provider and keys
      - monkeypatch getpass.getpass and boto3 to avoid real network calls
    """
    monkeypatch.setenv("HOME", str(tmp_path))

    # Install a fake boto3 module into sys.modules so validation uses it
    fake_boto3 = _make_fake_boto3_module()
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    # Monkeypatch getpass.getpass used by the CLI to read the secret
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "FAKESECRET")

    # Build the input sequence for typer.prompt:
    # 1) provider -> "aws"
    # 2) AWS Access Key ID -> "AKIAFAKE"
    # 3) Default region -> "us-east-1"
    # Note: getpass handles the secret separately.
    input_data = "aws\nAKIAFAKE\nus-east-1\n"

    result = runner.invoke(app, ["init"], input=input_data)
    assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"

    # Config file should exist and include aws creds metadata
    assert CONFIG_PATH.exists()
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert cfg["default_provider"] == "aws"
    assert "aws" in cfg["creds"]

    aws_meta = cfg["creds"]["aws"]
    assert aws_meta["access_key_id"] == "AKIAFAKE"
    # secret_meta should be present and be a dict describing storage method
    assert "secret_meta" in aws_meta and isinstance(aws_meta["secret_meta"], dict)
