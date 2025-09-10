# LCF/offload/manager.py
"""
OffloadManager - SQLite-backed offload queue and worker.

Features:
- enqueue(adapter, task_type, payload) to queue tasks (terraform|pulumi adapters supported)
- run_worker(...) loop that fetches pending tasks, dispatches them to adapters,
  persists line-by-line logs into offload_logs, and marks tasks done/failed with retries.
- dispatch_task public wrapper for programmatic dispatch.
- Backoff + retry (configurable via DEFAULT_MAX_ATTEMPTS / DEFAULT_BACKOFF_SEC).
"""

from __future__ import annotations
import sqlite3
import json
import time
import threading
import traceback
from typing import Optional, Dict, Any, List

# Adapters (must be importable)
from LCF.cloud_adapters.terraform_adapter import TerraformAdapter
from LCF.cloud_adapters import pulumi_adapter

SCHEMA = """
CREATE TABLE IF NOT EXISTS offload_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER,
  status TEXT,
  adapter TEXT,
  task_type TEXT,
  payload TEXT,
  attempts INTEGER DEFAULT 0,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS offload_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER,
  ts INTEGER,
  line TEXT
);
"""

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SEC = 5


class OffloadManager:
    def __init__(self, db_path: Optional[str] = "cloudbrew_offload.db"):
        self.path = db_path
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.cursor()
        cur.executescript(SCHEMA)
        cur.close()
        self._stop = threading.Event()

    # -----------------------
    # Task lifecycle / DB
    # -----------------------
    def enqueue(self, adapter: str, task_type: str, payload: Optional[Dict[str, Any]] = None) -> int:
        """
        Enqueue an offload task.

        adapter: 'terraform' | 'pulumi'
        task_type: 'apply_spec' | 'plan_spec' | 'apply_plan' | 'destroy' | 'destroy_stack' | ...
        payload: JSON-serializable dict (adapter-specific)
        """
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO offload_tasks(ts,status,adapter,task_type,payload) VALUES (?, ?, ?, ?, ?)",
            (int(time.time()), "pending", adapter, task_type, json.dumps(payload or {}))
        )
        task_id = cur.lastrowid
        self._conn.commit()
        cur.close()
        return task_id

    def fetch_pending(self, limit: int = 1) -> List[Dict[str, Any]]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM offload_tasks WHERE status = 'pending' ORDER BY id ASC LIMIT ?", (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows

    def mark(self, task_id: int, status: str, attempts: int = 0, last_error: Optional[str] = None):
        cur = self._conn.cursor()
        cur.execute(
            "UPDATE offload_tasks SET status = ?, attempts = ?, last_error = ? WHERE id = ?",
            (status, attempts, last_error, task_id)
        )
        self._conn.commit()
        cur.close()

    def mark_done(self, task_id: int):
        cur = self._conn.cursor()
        cur.execute("UPDATE offload_tasks SET status = ?, last_error = NULL WHERE id = ?", ("done", task_id))
        self._conn.commit()
        cur.close()

    def mark_failed(self, task_id: int, attempts: int, last_error: str):
        cur = self._conn.cursor()
        cur.execute("UPDATE offload_tasks SET status = ?, attempts = ?, last_error = ? WHERE id = ?",
                    ("failed", attempts, last_error, task_id))
        self._conn.commit()
        cur.close()

    # -----------------------
    # Logging
    # -----------------------
    def record_log(self, task_id: int, line: str):
        cur = self._conn.cursor()
        cur.execute("INSERT INTO offload_logs(task_id, ts, line) VALUES (?, ?, ?)",
                    (task_id, int(time.time()), line))
        self._conn.commit()
        cur.close()

    def get_logs(self, task_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        cur = self._conn.cursor()
        cur.execute("SELECT ts, line FROM offload_logs WHERE task_id = ? ORDER BY id DESC LIMIT ?", (task_id, limit))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows

    # -----------------------
    # Internal dispatch helpers
    # -----------------------
    def _dispatch_task(self, task_row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Internal dispatcher that executes a single task and streams logs via self.record_log.
        Returns a JSON-serializable result dict (adapter-specific summary).
        """
        task_id = int(task_row["id"])
        adapter = task_row["adapter"]
        task_type = task_row["task_type"]
        payload = json.loads(task_row.get("payload") or "{}")

        # small helper to consume generators and persist lines
        def consume_and_record(gen):
            for ln in gen:
                try:
                    line = ln if isinstance(ln, str) else json.dumps(ln)
                except Exception:
                    line = repr(ln)
                self.record_log(task_id, line)

        # -----------------------
        # Terraform adapter tasks
        # -----------------------
        if adapter == "terraform":
            ta = TerraformAdapter()

            # plan_spec: render HCL + run terraform plan (plan_only)
            if task_type == "plan_spec":
                logical_id = payload.get("logical_id", f"offload-{task_id}")
                spec = payload.get("spec", {})

                # Prefer create_instance() if implemented (synchronous summary)
                if callable(getattr(ta, "create_instance", None)):
                    res = ta.create_instance(logical_id, spec, plan_only=True)
                    self.record_log(task_id, json.dumps(res))
                    return res

                # Fallback to streaming if available
                if callable(getattr(ta, "stream_create_instance", None)):
                    gen = ta.stream_create_instance(logical_id, spec, plan_only=True)
                    consume_and_record(gen)
                    return {"status": "ok"}

                raise RuntimeError("terraform adapter has neither create_instance nor stream_create_instance")

            # apply_spec: create and apply
            if task_type == "apply_spec":
                logical_id = payload.get("logical_id", f"offload-{task_id}")
                spec = payload.get("spec", {})

                if callable(getattr(ta, "create_instance", None)):
                    res = ta.create_instance(logical_id, spec, plan_only=False)
                    self.record_log(task_id, json.dumps(res))
                    return res

                if callable(getattr(ta, "stream_create_instance", None)):
                    gen = ta.stream_create_instance(logical_id, spec, plan_only=False)
                    consume_and_record(gen)
                    return {"status": "ok"}

                raise RuntimeError("terraform adapter has neither create_instance nor stream_create_instance")

            # apply_plan: apply a previously saved plan file (plan_id path)
            if task_type == "apply_plan":
                plan_id = payload.get("plan_id")

                # Prefer apply_plan() if available
                if callable(getattr(ta, "apply_plan", None)):
                    res = ta.apply_plan(plan_id)
                    self.record_log(task_id, json.dumps(res))
                    return res

                if callable(getattr(ta, "stream_apply_plan", None)):
                    gen = ta.stream_apply_plan(plan_id)
                    consume_and_record(gen)
                    return {"status": "ok"}

                raise RuntimeError("terraform adapter has neither apply_plan nor stream_apply_plan")

            # destroy: remove resources (adapter-specific)
            if task_type == "destroy":
                adapter_id = payload.get("adapter_id")
                ok = ta.destroy_instance(adapter_id)
                self.record_log(task_id, json.dumps({"deleted": ok}))
                return {"deleted": ok}

            raise RuntimeError(f"unknown terraform task_type: {task_type}")

        # -----------------------
        # Pulumi adapter tasks
        # -----------------------
        elif adapter == "pulumi":
            if task_type == "plan_spec":
                spec = payload.get("spec", {})
                stack = payload.get("stack", "dev")
                gen = pulumi_adapter.plan(spec, stack)
                consume_and_record(gen)
                return {"status": "ok"}

            if task_type == "apply_spec":
                spec = payload.get("spec", {})
                stack = payload.get("stack", "dev")
                gen = pulumi_adapter.apply(spec, stack)
                consume_and_record(gen)
                return {"status": "ok"}

            if task_type == "destroy_stack":
                stack = payload.get("stack", "dev")
                gen = pulumi_adapter.destroy(stack)
                consume_and_record(gen)
                return {"status": "ok"}

            raise RuntimeError(f"unknown pulumi task_type: {task_type}")

        else:
            raise RuntimeError(f"unknown adapter: {adapter}")

    # -----------------------
    # Public dispatcher wrapper
    # -----------------------
    def dispatch_task(self, task_row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Public wrapper that forwards to the internal _dispatch_task.
        Useful for tests or programmatic invocation.
        """
        return self._dispatch_task(task_row)

    # -----------------------
    # Worker loop
    # -----------------------
    def run_worker(self, poll_interval: int = 5, concurrency: int = 1, max_attempts: int = DEFAULT_MAX_ATTEMPTS):
        """
        Worker loop that polls for pending tasks and dispatches them.
        - poll_interval: seconds to sleep when no tasks
        - concurrency: how many tasks to fetch at once (processed sequentially here)
        """
        print("[offload] worker starting")
        try:
            while not self._stop.is_set():
                tasks = self.fetch_pending(limit=concurrency)
                if not tasks:
                    time.sleep(poll_interval)
                    continue

                for t in tasks:
                    tid = int(t["id"])
                    attempts = int(t.get("attempts") or 0) + 1
                    try:
                        # mark running
                        self.mark(tid, "running", attempts=attempts)
                        self.record_log(tid, f"[offload] starting task {tid} adapter={t['adapter']} type={t['task_type']}")
                        # dispatch and capture result/logs
                        try:
                            res = self._dispatch_task(t)
                            self.record_log(tid, f"[offload] finished task {tid} result={json.dumps(res)[:200]}")
                            self.mark_done(tid)
                        except Exception as e:
                            tb = traceback.format_exc()
                            self.record_log(tid, f"[offload] exception: {str(e)}")
                            self.record_log(tid, tb)
                            if attempts >= max_attempts:
                                self.mark_failed(tid, attempts, str(e)[:1000])
                            else:
                                # re-queue after backoff
                                self.mark(tid, "pending", attempts=attempts, last_error=str(e)[:1000])
                                time.sleep(DEFAULT_BACKOFF_SEC)
                    except Exception as outer_e:
                        # unexpected worker-loop error: ensure it is logged and continue
                        tb = traceback.format_exc()
                        print("[offload] worker loop unexpected error:", outer_e)
                        try:
                            self.record_log(tid, f"[offload] worker loop error: {str(outer_e)}")
                            self.record_log(tid, tb)
                        except Exception:
                            # swallow errors here to avoid infinite crash
                            pass
                # small sleep to avoid hot-looping if many tasks
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("[offload] worker interrupted")
        finally:
            print("[offload] worker stopped")

    def stop(self):
        self._stop.set()
