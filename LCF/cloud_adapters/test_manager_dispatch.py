import json
from unittest import mock
from LCF.offload.manager import OffloadManager

def test_enqueue_and_fetch(tmp_path):
    db = str(tmp_path / "off.db")
    off = OffloadManager(db)
    tid = off.enqueue("terraform", "plan_spec", {"logical_id": "l1", "spec": {"image": "ubuntu"}})
    rows = off.fetch_pending()
    assert any(r["id"] == tid for r in rows)

@mock.patch("LCF.offload.manager.TerraformAdapter")
def test_dispatch_apply_spec_uses_adapter(mock_ta_class, tmp_path):
    db = str(tmp_path / "off.db")
    off = OffloadManager(db)
    mock_ta = mock_ta_class.return_value
    # simulate create_instance summary dict
    mock_ta.create_instance.return_value = {"success": True, "adapter_id": "tf-1"}
    tid = off.enqueue("terraform", "apply_spec", {"logical_id": "l1", "spec": {"image": "ubuntu"}})
    tasks = off.fetch_pending()
    # call public wrapper dispatch_task
    res = off.dispatch_task(tasks[0])
    assert res.get("success") is True
    logs = off.get_logs(tid)
    assert logs and len(logs) >= 1
