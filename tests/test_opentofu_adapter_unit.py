import pytest
from unittest import mock
from LCF.cloud_adapters.opentofu_adapter import OpenTofuAdapter

@pytest.fixture
def adapter():
    # Initialize without a DB path for testing
    return OpenTofuAdapter(db_path=None)

# -----------------------------------------------------------------------------
# Workdir & Init
# -----------------------------------------------------------------------------
def test_workdir_generation(adapter):
    """Ensure working directories are generated safely."""
    wd = adapter._workdir_for("my/unsafe:name")
    assert "my-unsafe-name" in wd
    assert ".cloudbrew_tofu" in wd

# -----------------------------------------------------------------------------
# Create / Plan
# -----------------------------------------------------------------------------
@mock.patch("LCF.cloud_adapters.opentofu_adapter.OpenTofuAdapter.stream_create_instance")
def test_create_instance_success(mock_stream, adapter):
    """Test create_instance returns correct dict on success."""
    # Mock generator output
    mock_stream.return_value = iter(["Initializing...", "APPLY_COMPLETE"])
    
    res = adapter.create_instance("vm-1", {"image": "ubuntu"}, plan_only=False)
    
    assert res["success"] is True
    assert res["adapter_id"] == "opentofu-vm-1"
    assert res["output"] == "APPLY_COMPLETE"

@mock.patch("LCF.cloud_adapters.opentofu_adapter.OpenTofuAdapter.stream_create_instance")
def test_plan_only_returns_plan_id(mock_stream, adapter):
    """Test plan() extracts the PLAN_SAVED path correctly."""
    mock_stream.return_value = iter(["Initializing...", "PLAN_SAVED:/tmp/plan.tfplan"])
    
    res = adapter.plan("vm-1", {"image": "ubuntu"})
    
    assert "plan_id" in res
    assert res["plan_id"] == "/tmp/plan.tfplan"

@mock.patch("LCF.cloud_adapters.opentofu_adapter.OpenTofuAdapter.stream_create_instance")
def test_create_instance_failure_handling(mock_stream, adapter):
    """Test exception handling during creation."""
    # Simulate a RuntimeError raised during streaming
    mock_stream.side_effect = RuntimeError("OpenTofu crashed")
    
    res = adapter.create_instance("vm-fail", {})
    
    assert res["success"] is False
    assert "OpenTofu crashed" in res["error"]

# -----------------------------------------------------------------------------
# Destroy
# -----------------------------------------------------------------------------
@mock.patch("LCF.cloud_adapters.opentofu_adapter.OpenTofuAdapter.stream_destroy_instance")
def test_destroy_instance_success(mock_stream, adapter):
    """Test destroy returns True on success."""
    mock_stream.return_value = iter(["Destroying...", "DESTROY_COMPLETE"])
    
    # Mock the store delete call
    with mock.patch.object(adapter.store, "delete_instance_by_adapter_id", return_value=True) as mock_db:
        success = adapter.destroy_instance("opentofu-vm-1")
        
        assert success is True
        mock_db.assert_called_with("opentofu-vm-1")

@mock.patch("LCF.cloud_adapters.opentofu_adapter.OpenTofuAdapter.stream_destroy_instance")
def test_destroy_instance_failure(mock_stream, adapter):
    """Test destroy returns False on exception."""
    mock_stream.side_effect = RuntimeError("Destroy failed")
    
    success = adapter.destroy_instance("opentofu-vm-1")
    assert success is False

# -----------------------------------------------------------------------------
# Drift Detection
# -----------------------------------------------------------------------------
@mock.patch("LCF.cloud_adapters.opentofu_adapter.subprocess.run")
@mock.patch("LCF.cloud_adapters.opentofu_adapter.os.path.exists")
def test_drift_detection_clean(mock_exists, mock_run, adapter):
    """Test drift check when infrastructure is clean (Exit Code 0)."""
    mock_exists.return_value = True # main.tf exists
    
    mock_proc = mock.Mock()
    mock_proc.returncode = 0
    mock_proc.stdout = "No changes."
    mock_run.return_value = mock_proc
    
    res = adapter.check_drift("vm-clean")
    
    assert res["drifted"] is False
    assert "up-to-date" in res["msg"]

@mock.patch("LCF.cloud_adapters.opentofu_adapter.subprocess.run")
@mock.patch("LCF.cloud_adapters.opentofu_adapter.os.path.exists")
def test_drift_detection_drifted(mock_exists, mock_run, adapter):
    """Test drift check when drift is detected (Exit Code 2)."""
    mock_exists.return_value = True
    
    mock_proc = mock.Mock()
    mock_proc.returncode = 2
    mock_proc.stdout = "Plan: 1 to add, 1 to change, 0 to destroy."
    mock_run.return_value = mock_proc
    
    res = adapter.check_drift("vm-drifted")
    
    assert res["drifted"] is True
    assert "1 to add" in res["summary"]

@mock.patch("LCF.cloud_adapters.opentofu_adapter.subprocess.run")
@mock.patch("LCF.cloud_adapters.opentofu_adapter.os.path.exists")
def test_drift_detection_error(mock_exists, mock_run, adapter):
    """Test drift check when Tofu errors out (Exit Code 1)."""
    mock_exists.return_value = True
    
    mock_proc = mock.Mock()
    mock_proc.returncode = 1
    mock_proc.stdout = "Syntax error in main.tf"
    mock_run.return_value = mock_proc
    
    res = adapter.check_drift("vm-error")
    
    assert res["drifted"] is None
    assert "Error" in res["msg"]