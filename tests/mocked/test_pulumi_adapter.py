"""
Unit tests for the PulumiAdapter class.

These tests verify the interaction between the PulumiAdapter and the Pulumi CLI,
ensuring that commands are constructed correctly and output is parsed as expected.
The actual Pulumi CLI execution is mocked to prevent side effects and dependencies on
an installed Pulumi binary.
"""
import pytest
from unittest import mock
from types import SimpleNamespace
from typing import Generator, Any
from LCF.cloud_adapters.pulumi_adapter import PulumiAdapter

@pytest.fixture
def adapter() -> PulumiAdapter:
    """Fixture to provide a fresh PulumiAdapter instance with a MOCKED store."""
    # 1. Initialize the adapter (this will create a real SQLiteStore internally initially)
    adapter_instance = PulumiAdapter()
    
    # 2. OVERRIDE the store with a MagicMock to prevent any DB operations
    # This prevents the sqlite3.ProgrammingError and avoids creating .db files
    mock_store = mock.MagicMock()
    adapter_instance.store = mock_store
    
    return adapter_instance

@mock.patch("LCF.cloud_adapters.pulumi_adapter.subprocess.Popen")
def test_plan_calls_preview(mock_popen: mock.MagicMock, adapter: PulumiAdapter) -> None:
    """
    Test that calling adapter.plan() triggers 'pulumi preview'.
    """
    # 1. Setup Mock Process
    mock_proc = SimpleNamespace()
    # Ensure stdout yields lines that the adapter expects or simply logs
    mock_proc.stdout = iter(["previewing update...", "summary: 1 to create"])
    mock_proc.wait = lambda: None
    mock_proc.returncode = 0
    mock_popen.return_value = mock_proc

    # 2. Define Inputs
    spec = {"name": "test-stack"}
    stack = "dev"

    # 3. Call Public API
    # plan() returns a dict like {'plan_id': '...', 'diff': '...'}
    result = adapter.plan(stack, spec) 
    
    # 4. Assertions
    assert isinstance(result, dict)
    assert "diff" in result
    assert "previewing update..." in result["diff"]
    
    # Verify arguments passed to subprocess
    # We check that 'preview' was called. Note that the adapter selects the stack first,
    # so 'preview' might not have the --stack argument explicitly.
    preview_call_found = False
    stack_select_found = False
    
    for call in mock_popen.call_args_list:
        args, _ = call
        cmd_list = args[0]
        
        # Check for stack selection or init
        if "stack" in cmd_list and ("select" in cmd_list or "init" in cmd_list):
            if stack in cmd_list:
                stack_select_found = True

        # Check for preview command
        if cmd_list[0] == "pulumi" and cmd_list[1] == "preview":
            preview_call_found = True
            assert "--non-interactive" in cmd_list
            # The adapter typically selects stack first, so --stack might not be here.
            # We verify stack selection above.
            break
    
    assert stack_select_found, "pulumi stack select/init was not called"
    assert preview_call_found, "pulumi preview command was not called"

@mock.patch("LCF.cloud_adapters.pulumi_adapter.subprocess.Popen")
def test_apply_calls_up(mock_popen: mock.MagicMock, adapter: PulumiAdapter) -> None:
    """
    Test that calling adapter.create_instance() triggers 'pulumi up --yes'.
    """
    mock_proc = SimpleNamespace()
    mock_proc.stdout = iter(["updating...", "update complete"])
    mock_proc.wait = lambda: None
    mock_proc.returncode = 0
    mock_popen.return_value = mock_proc

    spec = {"name": "test-stack"}
    logical_id = "test-stack"

    # 3. Call Public API
    res = adapter.create_instance(logical_id, spec, plan_only=False)
    
    # 4. Assertions
    assert res.get("success") is True
    assert "update complete" in res.get("output", "")

    # Check CLI args
    # We loop to find the 'up' command specifically
    up_call_found = False
    for call in mock_popen.call_args_list:
        args, _ = call
        cmd_list = args[0]
        if "up" in cmd_list:
            up_call_found = True
            assert "pulumi" in cmd_list
            assert "--yes" in cmd_list
            assert "--skip-preview" not in cmd_list # Adapter implementation specific
            break
    
    assert up_call_found, "pulumi up command was not called"

@mock.patch("LCF.cloud_adapters.pulumi_adapter.subprocess.Popen")
def test_destroy_calls_destroy(mock_popen: mock.MagicMock, adapter: PulumiAdapter) -> None:
    """
    Test that calling adapter.destroy_instance() triggers 'pulumi destroy --yes'.
    """
    mock_proc = SimpleNamespace()
    mock_proc.stdout = iter(["destroying...", "destroy complete"])
    mock_proc.wait = lambda: None
    mock_proc.returncode = 0
    mock_popen.return_value = mock_proc

    stack_name = "dev"
    # Protocol says adapter_id is usually "pulumi-<stack>"
    adapter_id = f"pulumi-{stack_name}"

    # 3. Call Public API
    success = adapter.destroy_instance(adapter_id)

    # 4. Assertions
    assert success is True
    
    destroy_call_found = False
    for call in mock_popen.call_args_list:
        args, _ = call
        cmd_list = args[0]
        if "destroy" in cmd_list:
            destroy_call_found = True
            assert "--yes" in cmd_list
            break
            
    assert destroy_call_found, "pulumi destroy command was not called"

@mock.patch("LCF.cloud_adapters.pulumi_adapter.subprocess.Popen")
def test_cli_failure_handling(mock_popen: mock.MagicMock, adapter: PulumiAdapter) -> None:
    """
    Test that a non-zero exit code raises an exception which bubbles up.
    """
    mock_proc = SimpleNamespace()
    mock_proc.stdout = iter(["error: authentication failed"])
    mock_proc.wait = lambda: None
    mock_proc.returncode = 1 # Simulate Failure
    mock_popen.return_value = mock_proc

    # 3. Call Public API and expect exception
    # create_instance calls _run_action -> _run_cli -> raises PulumiAdapterError
    with pytest.raises(Exception) as excinfo:
        adapter.create_instance("fail", {"name": "fail"}, plan_only=True)
    
    assert "failed with code 1" in str(excinfo.value)