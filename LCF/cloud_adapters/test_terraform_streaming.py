import pytest
from unittest import mock
from types import SimpleNamespace
from LCF.cloud_adapters import terraform_adapter

# simulate a streaming terraform subprocess via _stream_subprocess yield behaviour
@mock.patch("LCF.cloud_adapters.terraform_adapter.subprocess.Popen")
def test_stream_create_instance_fallback_and_plan(mock_popen, tmp_path, monkeypatch):
    # set no terraform binary to trigger fallback path
    monkeypatch.delenv("CLOUDBREW_TERRAFORM_BIN", raising=False)
    monkeypatch.setenv("CLOUDBREW_TF_FORCE_AWS", "0")
    # ensure terraform_path is None for adapter
    ta = terraform_adapter.TerraformAdapter(db_path=None)
    ta.terraform_path = None

    gen = ta.stream_create_instance("test-vm", {"provider": "aws", "image": "ubuntu-22.04", "size": "small"}, plan_only=True)
    lines = list(gen)
    assert any("fallback" in l or "PLAN_SAVED" in l or "would create resources" in l.lower() for l in lines)

@mock.patch("LCF.cloud_adapters.terraform_adapter.subprocess.Popen")
def test_stream_subprocess_raises_and_records_output(mock_popen):
    # create a fake process object emulating non-zero exit with output lines
    fake_proc = SimpleNamespace()
    fake_proc.stdout = iter(["line1\n", "ERR: fail\n"])
    fake_proc.wait = lambda timeout=None: None
    fake_proc.returncode = 1
    mock_popen.return_value = fake_proc

    # call _stream_subprocess directly and expect RuntimeError
    with pytest.raises(RuntimeError) as exc:
        list(terraform_adapter._stream_subprocess(["terraform", "plan"]))
    assert "failed with code" in str(exc.value)
