# LCF/offload/manager.py
"""
OffloadManager - SQLite-backed offload queue and worker.

Features:
- enqueue(adapter, task_type, payload) to queue tasks (terraform|pulumi adapters supported)
- run_worker(...) loop that fetches pending tasks, dispatches them to adapters,
  persists line-by-line logs into offload_logs, and marks tasks done/failed with retries.
- dispatch_task public wrapper for programmatic dispatch.
- Backoff + retry (configurable via DEFAULT_MAX_ATTEMPTS / DEFAULT_BACKOFF_SEC).
- Concurrency: multiple tasks executed in parallel threads.
"""

from __future__ import annotations
import sqlite3
import json
import time
import threading
import traceback
import shlex
import subprocess
from typing import Optional, Dict, Any, List, Iterable, Generator
from concurrent.futures import ThreadPoolExecutor, as_completed

# Adapters
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

# Tunables (can be overridden with environment variables if desired)
DEFAULT_MAX_ATTEMPTS = int((__import__("os").environ.get("CLOUDBREW_OFFLOAD_MAX_ATTEMPTS") or 3))
DEFAULT_BACKOFF_SEC = int((__import__("os").environ.get("CLOUDBREW_OFFLOAD_BACKOFF") or 5))
DEFAULT_POLL_INTERVAL = int((__import__("os").environ.get("CLOUDBREW_OFFLOAD_POLL") or 5))


class OffloadManager:
    def __init__(self, db_path: Optional[str] = "cloudbrew_offload.db"):
        self.path = db_path
        # sqlite connection shared between threads (check_same_thread=False)
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
        cur = self._conn.cursor()
        try:
            cur.execute(
                "INSERT INTO offload_tasks(ts,status,adapter,task_type,payload) VALUES (?, ?, ?, ?, ?)",
                (int(time.time()), "pending", adapter, task_type, json.dumps(payload or {}))
            )
            task_id = cur.lastrowid
            self._conn.commit()
            return task_id
        finally:
            cur.close()

    def fetch_pending(self, limit: int = 1) -> List[Dict[str, Any]]:
        cur = self._conn.cursor()
        try:
            cur.execute("SELECT * FROM offload_tasks WHERE status = 'pending' ORDER BY id ASC LIMIT ?", (limit,))
            rows = [dict(r) for r in cur.fetchall()]
            return rows
        finally:
            cur.close()

    def mark(self, task_id: int, status: str, attempts: int = 0, last_error: Optional[str] = None):
        cur = self._conn.cursor()
        try:
            cur.execute(
                "UPDATE offload_tasks SET status = ?, attempts = ?, last_error = ? WHERE id = ?",
                (status, attempts, last_error, task_id)
            )
            self._conn.commit()
        finally:
            cur.close()

    def mark_done(self, task_id: int):
        cur = self._conn.cursor()
        try:
            cur.execute("UPDATE offload_tasks SET status = ?, last_error = NULL WHERE id = ?", ("done", task_id))
            self._conn.commit()
        finally:
            cur.close()

    def mark_failed(self, task_id: int, attempts: int, last_error: str):
        cur = self._conn.cursor()
        try:
            cur.execute("UPDATE offload_tasks SET status = ?, attempts = ?, last_error = ? WHERE id = ?",
                        ("failed", attempts, last_error, task_id))
            self._conn.commit()
        finally:
            cur.close()

    # -----------------------
    # Logging
    # -----------------------
    def record_log(self, task_id: int, line: str):
        cur = self._conn.cursor()
        try:
            cur.execute("INSERT INTO offload_logs(task_id, ts, line) VALUES (?, ?, ?)",
                        (task_id, int(time.time()), line))
            self._conn.commit()
        finally:
            cur.close()

    def get_logs(self, task_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        cur = self._conn.cursor()
        try:
            cur.execute("SELECT ts, line FROM offload_logs WHERE task_id = ? ORDER BY id DESC LIMIT ?", (task_id, limit))
            rows = [dict(r) for r in cur.fetchall()]
            return rows
        finally:
            cur.close()

    # -----------------------
    # Helpers for consuming different return types
    # -----------------------
    def _consume_and_record(self, task_id: int, gen: Iterable):
        """
        Accepts either a generator yielding lines or a non-generator (dict/str).
        Writes lines to logs as they arrive.
        """
        # If gen is a generator/iterable of strings, iterate and record
        try:
            if hasattr(gen, "__iter__") and not isinstance(gen, (str, bytes, dict)):
                for ln in gen:
                    try:
                        line = ln if isinstance(ln, str) else json.dumps(ln)
                    except Exception:
                        line = repr(ln)
                    self.record_log(task_id, line)
            else:
                # single result (dict/string)
                try:
                    line = gen if isinstance(gen, str) else json.dumps(gen)
                except Exception:
                    line = repr(gen)
                self.record_log(task_id, line)
        except Exception as e:
            # ensure exceptions during consumption are propagated
            raise

    # -----------------------
    # Low-level shell runner for "shell" tasks
    # -----------------------
    def _run_shell(self, cmd: str, cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> Generator[str, None, None]:
        """
        Run a shell command and stream output lines.
        """
        # Use shlex.split for cross-platform safety when cmd is a single string.
        argv = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        assert proc.stdout is not None
        out_lines = []
        for line in proc.stdout:
            line = line.rstrip("\n")
            out_lines.append(line)
            yield line
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"shell command {' '.join(argv)} failed with code {proc.returncode}\n" + "\n".join(out_lines))

    # -----------------------
    # Internal dispatch helpers
    # -----------------------
    def _dispatch_task(self, task_row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a task and persist logs. Return a serializable result dict.
        """
        task_id = int(task_row["id"])
        adapter = task_row["adapter"]
        task_type = task_row["task_type"]
        payload = json.loads(task_row.get("payload") or "{}")

        # Terraform tasks
        if adapter == "terraform":
            ta = TerraformAdapter()

            # plan_spec: create plan only
            if task_type == "plan_spec":
                logical_id = payload.get("logical_id", f"offload-{task_id}")
                spec = payload.get("spec", {})
                # prefer create_instance
                if callable(getattr(ta, "create_instance", None)):
                    res = ta.create_instance(logical_id, spec, plan_only=True)
                    self.record_log(task_id, json.dumps(res))
                    return res
                # fallback streaming
                if callable(getattr(ta, "stream_create_instance", None)):
                    gen = ta.stream_create_instance(logical_id, spec, plan_only=True)
                    self._consume_and_record(task_id, gen)
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
                    self._consume_and_record(task_id, gen)
                    return {"status": "ok"}
                raise RuntimeError("terraform adapter has neither create_instance nor stream_create_instance")

            # apply_plan: apply a saved plan file/path
            if task_type == "apply_plan":
                # accept either plan_id or plan_path keys
                plan_id = payload.get("plan_id") or payload.get("plan_path") or payload.get("plan")
                if not plan_id:
                    raise RuntimeError("apply_plan requires plan_id or plan_path")
                if callable(getattr(ta, "apply_plan", None)):
                    res = ta.apply_plan(plan_id)
                    self.record_log(task_id, json.dumps(res))
                    return res
                if callable(getattr(ta, "stream_apply_plan", None)):
                    gen = ta.stream_apply_plan(plan_id)
                    self._consume_and_record(task_id, gen)
                    return {"status": "ok"}
                raise RuntimeError("terraform adapter has neither apply_plan nor stream_apply_plan")

            # destroy: remove resources (adapter-specific)
            if task_type == "destroy":
                adapter_id = payload.get("adapter_id")
                if not adapter_id:
                    raise RuntimeError("destroy requires adapter_id")
                ok = ta.destroy_instance(adapter_id)
                self.record_log(task_id, json.dumps({"deleted": ok}))
                return {"deleted": ok}

            raise RuntimeError(f"unknown terraform task_type: {task_type}")

        # Pulumi tasks
        elif adapter == "pulumi":
            # plan_spec
            if task_type == "plan_spec":
                logical_id = payload.get("logical_id", f"offload-{task_id}")
                spec = payload.get("spec", {})
                # pulumi_adapter may expose stream_create_instance or plan()
                if callable(getattr(pulumi_adapter, "plan", None)):
                    res = pulumi_adapter.plan(logical_id, spec) if False else pulumi_adapter.plan(spec, logical_id)  # handle both signatures
                    # plan() in PulumiAdapter may be generator or dict; handle both
                    if hasattr(res, "__iter__") and not isinstance(res, (str, dict)):
                        self._consume_and_record(task_id, res)
                        return {"status": "ok"}
                    else:
                        self.record_log(task_id, json.dumps(res))
                        return res
                if callable(getattr(pulumi_adapter, "stream_create_instance", None)):
                    gen = pulumi_adapter.stream_create_instance(logical_id, spec, plan_only=True)
                    self._consume_and_record(task_id, gen)
                    return {"status": "ok"}
                # try stream plan if exists
                if callable(getattr(pulumi_adapter, "stream_apply_plan", None)):
                    gen = pulumi_adapter.stream_apply_plan(f"pulumi-{logical_id}")
                    self._consume_and_record(task_id, gen)
                    return {"status": "ok"}
                raise RuntimeError("pulumi adapter missing plan/stream helpers")

            # apply_spec
            if task_type == "apply_spec":
                logical_id = payload.get("logical_id", f"offload-{task_id}")
                spec = payload.get("spec", {})
                if callable(getattr(pulumi_adapter, "create_instance", None)):
                    # signature might be (logical_id, spec) or (spec, logical_id) — prefer logical_id first
                    try:
                        res = pulumi_adapter.create_instance(logical_id, spec, plan_only=False)
                    except TypeError:
                        res = pulumi_adapter.create_instance(spec, logical_id, plan_only=False)
                    if hasattr(res, "__iter__") and not isinstance(res, (str, dict)):
                        self._consume_and_record(task_id, res)
                        return {"status": "ok"}
                    else:
                        self.record_log(task_id, json.dumps(res))
                        return res
                if callable(getattr(pulumi_adapter, "stream_create_instance", None)):
                    gen = pulumi_adapter.stream_create_instance(logical_id, spec, plan_only=False)
                    self._consume_and_record(task_id, gen)
                    return {"status": "ok"}
                raise RuntimeError("pulumi adapter missing create_instance/stream_create_instance")

            # apply_plan
            if task_type == "apply_plan":
                plan_id = payload.get("plan_id") or payload.get("plan_path") or payload.get("plan")
                if not plan_id:
                    raise RuntimeError("apply_plan requires plan_id or plan_path")
                if callable(getattr(pulumi_adapter, "apply_plan", None)):
                    res = pulumi_adapter.apply_plan(plan_id)
                    # may be generator or dict
                    if hasattr(res, "__iter__") and not isinstance(res, (str, dict)):
                        self._consume_and_record(task_id, res)
                        return {"status": "ok"}
                    else:
                        self.record_log(task_id, json.dumps(res))
                        return res
                if callable(getattr(pulumi_adapter, "stream_apply_plan", None)):
                    gen = pulumi_adapter.stream_apply_plan(plan_id)
                    self._consume_and_record(task_id, gen)
                    return {"status": "ok"}
                raise RuntimeError("pulumi adapter missing apply_plan/stream_apply_plan")

            # destroy_stack
            if task_type == "destroy_stack":
                stack = payload.get("stack")
                if not stack:
                    raise RuntimeError("destroy_stack requires stack name")
                if callable(getattr(pulumi_adapter, "destroy", None)):
                    gen = pulumi_adapter.destroy(stack)
                    self._consume_and_record(task_id, gen)
                    return {"status": "ok"}
                if callable(getattr(pulumi_adapter, "stream_destroy_instance", None)):
                    gen = pulumi_adapter.stream_destroy_instance(stack)
                    self._consume_and_record(task_id, gen)
                    return {"status": "ok"}
                raise RuntimeError("pulumi adapter missing destroy helpers")

            raise RuntimeError(f"unknown pulumi task_type: {task_type}")

        # Shell / other tasks
        elif adapter == "shell":
            if task_type == "run":
                cmd = payload.get("cmd")
                if not cmd:
                    raise RuntimeError("shell.run requires 'cmd' in payload")
                gen = self._run_shell(cmd, cwd=payload.get("cwd"), env=payload.get("env"))
                self._consume_and_record(task_id, gen)
                return {"status": "ok"}
            raise RuntimeError(f"unknown shell task_type: {task_type}")

        else:
            raise RuntimeError(f"unknown adapter: {adapter}")

    # -----------------------
    # Public dispatcher wrapper
    # -----------------------
    def dispatch_task(self, task_row: Dict[str, Any]) -> Dict[str, Any]:
        return self._dispatch_task(task_row)

    # -----------------------
    # Worker loop with concurrency
    # -----------------------
    def run_worker(self, poll_interval: int = DEFAULT_POLL_INTERVAL, concurrency: int = 1, max_attempts: int = DEFAULT_MAX_ATTEMPTS):
        """
        Worker loop with optional concurrency.
        - poll_interval: seconds to sleep when no tasks
        - concurrency: number of parallel worker threads
        - max_attempts: retries before marking failed
        """
        print(f"[offload] worker starting (concurrency={concurrency}, poll_interval={poll_interval})")
        executor = ThreadPoolExecutor(max_workers=max(1, concurrency))
        try:
            while not self._stop.is_set():
                tasks = self.fetch_pending(limit=concurrency)
                if not tasks:
                    time.sleep(poll_interval)
                    continue

                futures = {}
                for t in tasks:
                    tid = int(t["id"])
                    attempts = int(t.get("attempts") or 0) + 1
                    try:
                        # mark running
                        self.mark(tid, "running", attempts=attempts)
                        self.record_log(tid, f"[offload] starting task {tid} adapter={t['adapter']} type={t['task_type']}")
                        fut = executor.submit(self._dispatch_task, t)
                        futures[fut] = (tid, attempts)
                    except Exception as e:
                        tb = traceback.format_exc()
                        self.record_log(tid, f"[offload] error scheduling task {tid}: {e}")
                        self.record_log(tid, tb)
                        # push back to pending so it can be retried later
                        self.mark(tid, "pending", attempts=attempts, last_error=str(e)[:1000])

                # await completion and persist results
                for fut in as_completed(list(futures.keys())):
                    tid, attempts = futures[fut]
                    try:
                        res = fut.result()
                        self.record_log(tid, f"[offload] finished task {tid} result={json.dumps(res)[:200]}")
                        self.mark_done(tid)
                    except Exception as e:
                        tb = traceback.format_exc()
                        self.record_log(tid, f"[offload] exception during execution: {e}")
                        self.record_log(tid, tb)
                        if attempts >= max_attempts:
                            self.mark_failed(tid, attempts, str(e)[:1000])
                        else:
                            # re-queue after backoff
                            self.mark(tid, "pending", attempts=attempts, last_error=str(e)[:1000])
                            time.sleep(DEFAULT_BACKOFF_SEC)

                # light sleep to avoid busy loop
                time.sleep(0.05)

        except KeyboardInterrupt:
            print("[offload] worker interrupted")
        finally:
            executor.shutdown(wait=True)
            print("[offload] worker stopped")

    def stop(self):
        self._stop.set()
