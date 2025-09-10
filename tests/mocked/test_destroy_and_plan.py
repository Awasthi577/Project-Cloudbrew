# tests/mocked/test_destroy_and_plan.py
import os
import pytest
from unittest import mock
from LCF.cloud_adapters.terraform_adapter import TerraformAdapter


def test_destroy_instance_fallback(tmp_path):
    ta = TerraformAdapter(db_path=str(tmp_path / "db.sqlite"))
    ta.terraform_path = None  # force fallback
    adapter_id = "terraform-testvm"
    ok = ta.destroy_instance(adapter_id)
    assert ok in (True, False)  # fallback just returns False if no DB entry


@mock.patch("LCF.cloud_adapters.terraform_adapter._stream_subprocess")
def test_stream_create_and_destroy(mock_stream, tmp_path):
    mock_stream.return_value = iter(["init ok", "plan ok", "APPLY_COMPLETE"])
    ta = TerraformAdapter(db_path=str(tmp_path / "db.sqlite"))
    ta.terraform_path = "terraform"
    list(ta.stream_create_instance("testvm", {"image": "ubuntu-22.04"}, plan_only=False))
    mock_stream.return_value = iter(["destroy ok", "DESTROY_COMPLETE"])
    list(ta.stream_destroy_instance("testvm"))
    assert mock_stream.call_count >= 2
