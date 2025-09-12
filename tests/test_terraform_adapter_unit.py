import os
import json
import tempfile
from unittest.mock import patch
from LCF.cloud_adapters.terraform_adapter import TerraformAdapter

def test_translate_and_plan_fallback(tmp_path, monkeypatch):
    ta = TerraformAdapter(db_path=None)
    # force terraform binary missing to exercise fallback behavior
    monkeypatch.setattr(ta, "terraform_path", None)
    spec = {"name": "t1", "type": "vm", "image": "ubuntu-22.04", "size": "small", "region": "us-east-1"}
    gen = ta.stream_create_instance("test-vm", spec, plan_only=True)
    out = list(gen)
    # fallback should include "PLAN_SAVED" when plan_only
    assert any("PLAN_SAVED" in line for line in out)

def test_build_apply_cmd():
    from LCF.cloud_adapters.terraform_adapter import _build_terraform_apply_cmd
    cmd = _build_terraform_apply_cmd("terraform", "path/to/plan.tfplan")
    assert "apply" in cmd[1]
    assert "-auto-approve" in cmd
    # ensure plan path is last token
    assert cmd[-1] == "path/to/plan.tfplan"

def test_apply_plan_calls(monkeypatch, tmp_path):
    ta = TerraformAdapter(db_path=None)
    # simulate terraform CLI present but stub _run/_stream_subprocess to avoid running real process
    monkeypatch.setattr(ta, "terraform_path", "/usr/bin/terraform")
    monkeypatch.setattr("LCF.cloud_adapters.terraform_adapter._stream_subprocess", lambda *a, **k: iter(["line1","line2"]))
    res = ta.apply_plan(str(tmp_path/"dummy.plan"))
    # stream_apply_plan returns success dict or raises; adapt as needed
    assert isinstance(res, dict)
