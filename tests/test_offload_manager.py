import json
import sqlite3
from LCF.offload.manager import OffloadManager
from unittest.mock import patch

def test_enqueue_and_dispatch(tmp_path, monkeypatch):
    db = str(tmp_path/"off.db")
    off = OffloadManager(db_path=db)

    # enqueue a terraform apply_plan with a fake plan_id
    pid = off.enqueue("terraform", "apply_plan", {"plan_id":"dummy-plan"})
    assert pid > 0

    # monkeypatch TerraformAdapter.apply_plan to return a known structure
    class FakeTA:
        def apply_plan(self, plan_path):
            return {"success": True, "adapter_id": "terraform-dummy", "output": "applied"}
    monkeypatch.setattr("LCF.offload.manager.TerraformAdapter", lambda *args, **kwargs: FakeTA())

    # fetch pending and dispatch single row
    tasks = off.fetch_pending(limit=1)
    assert len(tasks) == 1
    res = off.dispatch_task(tasks[0])
    assert isinstance(res, dict)