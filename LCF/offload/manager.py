# LCF/offload/manager.py
import sqlite3
import json
import time
import subprocess
import threading
from typing import Optional, Dict, Any, List

SCHEMA = """
CREATE TABLE IF NOT EXISTS offload_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER,
  status TEXT,
  provider TEXT,
  cmd TEXT,
  payload TEXT,
  attempts INTEGER DEFAULT 0,
  last_error TEXT
);
"""

class OffloadManager:
    def __init__(self, db_path: Optional[str] = "cloudbrew_offload.db"):
        self.path = db_path
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.cursor()
        cur.executescript(SCHEMA)
        cur.close()
        self._stop = threading.Event()

    def enqueue(self, provider: str, cmd: str, payload: Optional[Dict[str,Any]] = None) -> int:
        cur = self._conn.cursor()
        cur.execute("INSERT INTO offload_tasks(ts,status,provider,cmd,payload) VALUES (?, ?, ?, ?, ?)",
                    (int(time.time()), "pending", provider, cmd, json.dumps(payload or {})))
        task_id = cur.lastrowid
        self._conn.commit()
        cur.close()
        return task_id

    def fetch_pending(self, limit: int = 1) -> List[Dict[str,Any]]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM offload_tasks WHERE status = 'pending' ORDER BY id ASC LIMIT ?", (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows

    def mark(self, task_id: int, status: str, attempts: int = 0, last_error: Optional[str] = None):
        cur = self._conn.cursor()
        cur.execute("UPDATE offload_tasks SET status = ?, attempts = ?, last_error = ? WHERE id = ?", (status, attempts, last_error, task_id))
        self._conn.commit()
        cur.close()

    def run_worker(self, poll_interval: int = 5, concurrency: int = 1):
        print("[offload] worker starting")
        try:
            while not self._stop.is_set():
                tasks = self.fetch_pending(limit=concurrency)
                if not tasks:
                    time.sleep(poll_interval)
                    continue
                for t in tasks:
                    tid = t["id"]
                    try:
                        # mark running
                        self.mark(tid, "running", attempts=int(t["attempts"] or 0) + 1)
                        cmd = t["cmd"]
                        print(f"[offload] running task {tid}: {cmd}")
                        # very simple: run shell command
                        proc = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=3600)
                        out = proc.stdout
                        if proc.returncode == 0:
                            self.mark(tid, "done", attempts=int(t["attempts"] or 0) + 1)
                            print(f"[offload] task {tid} done")
                        else:
                            self.mark(tid, "failed", attempts=int(t["attempts"] or 0) + 1, last_error=out[:1000])
                            print(f"[offload] task {tid} failed: {proc.returncode}")
                    except Exception as e:
                        self.mark(tid, "failed", attempts=int(t["attempts"] or 0) + 1, last_error=str(e)[:1000])
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("[offload] worker interrupted")
        finally:
            print("[offload] worker stopped")

    def stop(self):
        self._stop.set()
