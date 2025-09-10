import pytest
from unittest import mock
from LCF.cloud_adapters import pulumi_adapter
import os

def test_make_project_template_creates_files(tmp_path):
    d = str(tmp_path / "proj")
    pulumi_adapter._make_project_template(d)
    assert os.path.exists(os.path.join(d, "Pulumi.yaml"))
    assert os.path.exists(os.path.join(d, "requirements.txt"))
    assert os.path.exists(os.path.join(d, "__main__", "__main__.py"))

@mock.patch("LCF.cloud_adapters.pulumi_adapter._stream_subprocess")
def test_run_cli_preview_calls_stream(mock_stream):
    mock_stream.return_value = iter(["preview line1", "preview done"])
    spec = {"resources": []}
    project_dir = pulumi_adapter._make_project_dir()
    try:
        # ensure template exists
        pulumi_adapter._make_project_template(project_dir)
        lines = list(pulumi_adapter._run_cli(project_dir, spec, "stack-test", "preview"))
        assert any("preview" in l.lower() or "stack" in l.lower() or "done" in l.lower() for l in lines)
    finally:
        import shutil
        shutil.rmtree(project_dir, ignore_errors=True)

@mock.patch("subprocess.Popen")
def test_stream_subprocess_reads_stdout(mock_popen):
    # arrange: make a fake process with stdout iterable
    class FakeStdout:
        def __init__(self, lines):
            self._lines = lines
        def __iter__(self):
            return iter(self._lines)
        def close(self):
            pass

    fake_proc = mock.Mock()
    fake_proc.stdout = FakeStdout(["line1\n", "line2\n"])
    fake_proc.wait.return_value = 0
    fake_proc.returncode = 0
    mock_popen.return_value = fake_proc

    lines = list(pulumi_adapter._stream_subprocess(["pulumi", "up"]))
    assert lines == ["line1", "line2"]

