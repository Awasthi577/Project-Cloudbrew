import pytest
from unittest import mock
from LCF.cloud_adapters.terraform_adapter import _run

@mock.patch("subprocess.Popen")
def test_run_missing_executable(mock_popen):
    # simulate FileNotFoundError
    def fake_popen(*args, **kwargs):
        raise FileNotFoundError("no exe")
    mock_popen.side_effect = fake_popen
    rc, out = _run(["nonexistent"])
    assert rc == 127
    assert "executable not found" in out

@mock.patch("subprocess.Popen")
def test_run_success_streams(mock_popen):
    class FakeStdout:
        def __init__(self, lines):
            self._lines = lines
        def __iter__(self):
            return iter(self._lines)
        def close(self):
            pass
    fake_proc = mock.Mock()
    fake_proc.stdout = FakeStdout(["ok\n"])
    fake_proc.wait.return_value = 0
    fake_proc.returncode = 0
    mock_popen.return_value = fake_proc
    rc, out = _run(["echo"])
    assert rc == 0
    assert "ok" in out
