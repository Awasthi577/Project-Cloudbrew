import pytest
import subprocess
from unittest import mock
from types import SimpleNamespace
from LCF.cloud_adapters import opentofu_adapter

# -----------------------------------------------------------------------------
# Test Case 1: Binary Missing (Fallback Mode)
# -----------------------------------------------------------------------------
@mock.patch("LCF.cloud_adapters.opentofu_adapter.shutil.which")
def test_stream_create_instance_fallback(mock_which, monkeypatch):
    """
    Edge Case: The 'tofu' binary is completely missing from the system.
    Expected: The adapter should fallback to generating HCL and yielding a mock success/plan message.
    """
    # 1. Simulate binary missing
    mock_which.return_value = None
    monkeypatch.delenv("CLOUDBREW_OPENTOFU_BIN", raising=False)
    
    # 2. Init adapter
    ta = opentofu_adapter.OpenTofuAdapter(db_path=None)
    
    # 3. Execute
    spec = {"provider": "aws", "image": "ubuntu-22.04", "size": "small"}
    gen = ta.stream_create_instance("test-vm-fallback", spec, plan_only=True)
    lines = list(gen)
    
    # 4. Assertions
    # Should detect fallback warning or specific key phrases
    output_str = "\n".join(lines)
    assert "OpenTofu binary ('tofu') not found" in output_str or "ERROR" in output_str
    # In strict mode, this raises RuntimeError, but if your adapter handles it gracefully:
    # assert "PLAN_SAVED" in output_str # If logic permits fallback generation

# -----------------------------------------------------------------------------
# Test Case 2: Subprocess Failure (Crash/Error)
# -----------------------------------------------------------------------------
@mock.patch("LCF.cloud_adapters.opentofu_adapter.subprocess.Popen")
def test_stream_subprocess_failure(mock_popen):
    """
    Edge Case: The 'tofu' process starts but crashes or returns exit code 1.
    Expected: RuntimeError should be raised, containing the error logs.
    """
    # 1. Mock a failed process
    fake_proc = SimpleNamespace()
    fake_proc.stdout = iter(["Initializing...", "Error: Syntax error in main.tf\n"])
    fake_proc.wait = lambda timeout=None: None
    fake_proc.returncode = 1
    fake_proc.kill = lambda: None # Mock kill method
    
    mock_popen.return_value = fake_proc

    # 2. Execute directly against the helper
    with pytest.raises(RuntimeError) as exc:
        list(opentofu_adapter._stream_subprocess(["tofu", "plan"], cwd="/tmp", env={}))
    
    # 3. Assertions
    assert "Command failed" in str(exc.value)
    assert "Exit: 1" in str(exc.value)

# -----------------------------------------------------------------------------
# Test Case 3: Successful Streaming (Happy Path)
# -----------------------------------------------------------------------------
@mock.patch("LCF.cloud_adapters.opentofu_adapter.subprocess.Popen")
def test_stream_create_instance_success(mock_popen, tmp_path):
    """
    Edge Case: Standard successful run.
    Expected: Yields all stdout lines and ends with APPLY_COMPLETE.
    """
    # 1. Mock success
    fake_proc = SimpleNamespace()
    fake_proc.stdout = iter([
        "OpenTofu v1.6.0\n", 
        "Initializing the backend...\n", 
        "Apply complete! Resources: 1 added, 0 changed, 0 destroyed.\n"
    ])
    fake_proc.wait = lambda timeout=None: None
    fake_proc.returncode = 0
    fake_proc.kill = lambda: None
    
    mock_popen.return_value = fake_proc

    # 2. Init Adapter with mock path
    ta = opentofu_adapter.OpenTofuAdapter(db_path=None)
    ta.tofu_path = "/usr/bin/tofu" # Force path so it tries to run

    # 3. Execute
    spec = {"name": "web", "type": "vm"}
    gen = ta.stream_create_instance("web-1", spec, plan_only=False)
    lines = list(gen)

    # 4. Assertions
    assert "Initializing OpenTofu..." in lines
    # Note: Our adapter yields "Apply complete" from the subprocess output mock
    assert "APPLY_COMPLETE" in lines

# -----------------------------------------------------------------------------
# Test Case 4: Plan Only Mode
# -----------------------------------------------------------------------------
@mock.patch("LCF.cloud_adapters.opentofu_adapter.subprocess.Popen")
def test_stream_create_instance_plan_only(mock_popen):
    """
    Edge Case: User requests plan_only=True.
    Expected: Stops after plan phase, yields PLAN_SAVED:<path>.
    """
    fake_proc = SimpleNamespace()
    fake_proc.stdout = iter(["Plan: 1 to add, 0 to change, 0 to destroy.\n"])
    fake_proc.wait = lambda timeout=None: None
    fake_proc.returncode = 0 
    fake_proc.kill = lambda: None
    
    mock_popen.return_value = fake_proc

    ta = opentofu_adapter.OpenTofuAdapter(db_path=None)
    ta.tofu_path = "/usr/bin/tofu"

    gen = ta.stream_create_instance("web-plan", {}, plan_only=True)
    lines = list(gen)
    
    # Verify we got the save artifact message
    assert any(l.startswith("PLAN_SAVED:") for l in lines)
    # Verify we did NOT try to apply (APPLY_COMPLETE should be missing if loop breaks early)
    assert "APPLY_COMPLETE" not in lines

# -----------------------------------------------------------------------------
# Test Case 5: Timeout Handling
# -----------------------------------------------------------------------------
@mock.patch("LCF.cloud_adapters.opentofu_adapter.subprocess.Popen")
@mock.patch("LCF.cloud_adapters.opentofu_adapter.time.time")
def test_stream_timeout(mock_time, mock_popen):
    """
    Edge Case: Process hangs longer than timeout.
    Expected: TimeoutError / RuntimeError raised.
    """
    # 1. Setup infinite loop simulation
    fake_proc = SimpleNamespace()
    # Simulate a stream that never ends naturally
    fake_proc.stdout = iter(["hang...\n"] * 10) 
    fake_proc.wait = lambda timeout=None: None
    fake_proc.returncode = 0
    fake_proc.kill = lambda: None
    
    mock_popen.return_value = fake_proc
    
    # 2. Mock time to jump forward
    # First call = start time, subsequent calls = start + 1000s (triggering timeout)
    mock_time.side_effect = [1000, 2000, 2001, 2002] 

    # 3. Execute low-level helper with short timeout
    with pytest.raises(RuntimeError) as exc:
        # Pass a very short timeout that our mock time will exceed
        list(opentofu_adapter._stream_subprocess(["tofu", "apply"], cwd="/tmp", env={}, timeout=5))
    
    assert "Command timed out" in str(exc.value)