import json
import sqlite3
from LCF.offload.manager import OffloadManager
from unittest.mock import patch, MagicMock

def test_enqueue_and_dispatch(tmp_path, monkeypatch):
    """
    Verifies that the OffloadManager correctly enqueues a task for the 'opentofu'
    adapter and dispatches it using the mocked OpenTofuAdapter.
    """
    db = str(tmp_path / "off.db")
    off = OffloadManager(db_path=db)

    # 1. Enqueue an OpenTofu 'apply_plan' task
    # Use "opentofu" as the adapter key
    pid = off.enqueue("opentofu", "apply_plan", {"plan_id": "dummy-plan"})
    assert pid  # Ensure we got a valid ID (string UUID usually, but truthy check is fine for "exists")

    # 2. Mock OpenTofuAdapter
    # We create a mock class that behaves like the real adapter
    mock_adapter_instance = MagicMock()
    mock_adapter_instance.apply_plan.return_value = {
        "success": True, 
        "adapter_id": "opentofu-dummy", 
        "output": "applied successfully"
    }

    # Monkeypatch where the class is *instantiated* or *used*.
    # In LCF.offload.manager.dispatch_task, it likely does:
    #   if adapter_name == "opentofu": adapter = OpenTofuAdapter()
    # So we patch the class in that module namespace.
    monkeypatch.setattr("LCF.offload.manager.OpenTofuAdapter", lambda *args, **kwargs: mock_adapter_instance)

    # 3. Fetch pending task
    tasks = off.fetch_pending(limit=1)
    assert len(tasks) == 1
    task = tasks[0]
    assert task["adapter"] == "opentofu"
    assert task["task_type"] == "apply_plan"

    # 4. Dispatch the task
    res = off.dispatch_task(task)

    # 5. Verify results
    assert isinstance(res, dict)
    assert res["success"] is True
    assert res["adapter_id"] == "opentofu-dummy"
    
    # Verify the adapter method was actually called with expected args
    mock_adapter_instance.apply_plan.assert_called_once_with("dummy-plan")