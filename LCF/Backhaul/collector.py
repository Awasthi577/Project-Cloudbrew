# LCF/backhaul/collector.py
"""
Backhaul collector: simple SQLite-backed store for plan/apply events.
Provides:
  - Collector.record_plan(plan_event) -> run_id
  - Collector.record_apply(plan_id, apply_event)
  - Collector.query_runs(...)
Simple, safe defaults and small text excerpt storage.
"""

from __future__ import annotations
import sqlite3
import json
import time
import hashlib
from typing import Dict, Any, Optional, List

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER,
  logical_id TEXT,
  provider TEXT,
  spec_hash TEXT,
  spec_json TEXT,
  plan_id TEXT,
  plan_hash TEXT,
  add_count INTEGER,
  change_count INTEGER,
  destroy_count INTEGER,
  plan_excerpt TEXT,
  apply_success INTEGER,
  apply_duration REAL,
  apply_output_excerpt TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_logical ON runs(logical_id);
CREATE INDEX IF NOT EXISTS idx_runs_plan ON runs(plan_id);
"""

def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, sort_keys=True)
    except Exception:
        return json.dumps(str(obj))

def _hash_string(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class Collector:
    def __init__(self, path: str = "backhaul.db"):
        self.path = path
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.cursor()
        cur.executescript(SCHEMA)
        cur.close()
        self._conn.commit()

    def _spec_hash(self, spec: Dict[str, Any]) -> str:
        return _hash_string(_safe_json(spec))

    def _plan_hash(self, plan_text: str) -> str:
        return _hash_string(plan_text or "")

    def _excerpt(self, s: Optional[str], limit: int = 2000) -> str:
        if not s:
            return ""
        return s[:limit]

    def record_plan(self, plan_event: Dict[str, Any]) -> int:
        """
        plan_event expected keys:
          logical_id, provider, spec (dict), plan_id (str), plan_summary {add,change,destroy}, plan_text (str)
        Returns run_id (int)
        """
        ts = int(time.time())
        logical_id = plan_event.get("logical_id")
        provider = plan_event.get("provider")
        spec = plan_event.get("spec", {})
        plan_id = plan_event.get("plan_id")
        plan_text = plan_event.get("plan_text") or plan_event.get("diff") or ""
        summary = plan_event.get("plan_summary", {})
        add = int(summary.get("add", 0))
        change = int(summary.get("change", 0))
        destroy = int(summary.get("destroy", 0))

        spec_json = _safe_json(spec)
        spec_hash = self._spec_hash(spec)
        plan_hash = self._plan_hash(plan_text)
        excerpt = self._excerpt(plan_text)

        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO runs(ts, logical_id, provider, spec_hash, spec_json, plan_id, plan_hash, add_count, change_count, destroy_count, plan_excerpt, apply_success, apply_duration, apply_output_excerpt) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, logical_id, provider, spec_hash, spec_json, plan_id, plan_hash, add, change, destroy, excerpt, None, None, None),
        )
        run_id = cur.lastrowid
        self._conn.commit()
        cur.close()
        return run_id

    def record_apply(self, plan_id: str, apply_event: Dict[str, Any]) -> bool:
        """
        apply_event expected keys:
          success (bool), duration_s (float), output (str)
        Updates the runs row with matching plan_id.
        Returns True if updated a row.
        """
        success = 1 if apply_event.get("success") else 0
        dur = float(apply_event.get("duration_s") or apply_event.get("duration") or 0.0)
        out = apply_event.get("output") or apply_event.get("stdout") or ""
        excerpt = self._excerpt(out)

        cur = self._conn.cursor()
        cur.execute("UPDATE runs SET apply_success = ?, apply_duration = ?, apply_output_excerpt = ? WHERE plan_id = ?", (success, dur, excerpt, plan_id))
        updated = cur.rowcount
        self._conn.commit()
        cur.close()
        return updated > 0

    # small helpers for inspection
    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows

    def get_run_by_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM runs WHERE plan_id = ? ORDER BY id DESC LIMIT 1", (plan_id,))
        r = cur.fetchone()
        cur.close()
        return dict(r) if r else None
