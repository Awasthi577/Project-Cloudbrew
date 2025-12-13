import json
from unittest import mock
from LCF.offload.manager import OffloadManager

def test_enqueue_and_fetch(tmp_path):
    """
    Verify we can enqueue a task for the 'opentofu' provider.
    """
    db = str(tmp_path / "off.db")
    off = OffloadManager(db)
    tid = off.enqueue("opentofu", "plan_spec", {"logical_id": "l1", "spec": {"image": "ubuntu"}})
    rows = off.fetch_pending()
    assert any(r["id"] == tid for r in rows)

# We patch where the class is USED (in offload manager), but pointing to the NEW adapter
@mock.patch("LCF.offload.manager.OpenTofuAdapter")
def test_dispatch_apply_spec_uses_adapter(mock_tofu_class, tmp_path):
    """
    Verify that an 'opentofu' task correctly instantiates OpenTofuAdapter.
    """
    db = str(tmp_path / "off.db")
    off = OffloadManager(db)
    
    mock_adapter = mock_tofu_class.return_value
    # Simulate success response from OpenTofu
    mock_adapter.create_instance.return_value = {"success": True, "adapter_id": "tofu-1"}
    
    # Enqueue task for "opentofu"
    tid = off.enqueue("opentofu", "apply_spec", {"logical_id": "l1", "spec": {"image": "ubuntu"}})
    
    tasks = off.fetch_pending()
    
    # This calls offload.manager.dispatch_task -> imports OpenTofuAdapter -> calls create_instance
    res = off.dispatch_task(tasks[0])
    
    assert res.get("success") is True
    # Verify the mock was called
    mock_adapter.create_instance.assert_called_once()
    
    logs = off.get_logs(tid)
    assert logs and len(logs) >= 1