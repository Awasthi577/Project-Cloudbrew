# LCF/store.py
import json
import sqlite3
import time
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS instances (
  logical_id TEXT PRIMARY KEY,
  adapter TEXT,
  adapter_id TEXT,
  spec_json TEXT,
  state TEXT,
  created_at INTEGER
);
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER,
  action TEXT,
  details TEXT
);
CREATE INDEX IF NOT EXISTS idx_instances_adapter ON instances(adapter);
CREATE INDEX IF NOT EXISTS idx_instances_adapter_id ON instances(adapter_id);
CREATE INDEX IF NOT EXISTS idx_instances_logical_prefix ON instances(logical_id);
"""


def _now_ts() -> int:
    return int(time.time())


class SQLiteStore:
    def __init__(self, path: str | None = None):
        self.path = path or ":memory:"
        # allow multi-thread use (simple); enable WAL for better concurrency
        self._conn = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._setup_pragmas()
        self._init()

    def _setup_pragmas(self):
        cur = self._conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.close()

    def _init(self):
        cur = self._conn.cursor()
        cur.executescript(SCHEMA)
        cur.close()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    # primary upsert used by adapters
    def upsert_instance(self, inst: Dict[str, Any]):
        spec_json = json.dumps(inst.get("spec") or {})
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO instances(logical_id, adapter, adapter_id, spec_json, state, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(logical_id) DO UPDATE SET
              adapter=excluded.adapter,
              adapter_id=excluded.adapter_id,
              spec_json=excluded.spec_json,
              state=excluded.state,
              created_at=excluded.created_at
        """,
            (
                inst["logical_id"],
                inst.get("adapter"),
                inst.get("adapter_id"),
                spec_json,
                inst.get("state"),
                inst.get("created_at", _now_ts()),
            ),
        )
        cur.close()

    def delete_instance_by_adapter_id(self, adapter_id: str) -> bool:
        cur = self._conn.cursor()
        cur.execute("DELETE FROM instances WHERE adapter_id = ?", (adapter_id,))
        rowcount = cur.rowcount
        cur.close()
        return rowcount > 0

    def get_instance(self, logical_id: str) -> Optional[Dict]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM instances WHERE logical_id = ?", (logical_id,))
        r = cur.fetchone()
        cur.close()
        if not r:
            return None
        return {
            "logical_id": r["logical_id"],
            "adapter": r["adapter"],
            "adapter_id": r["adapter_id"],
            "state": r["state"],
            "spec": json.loads(r["spec_json"] or "{}"),
            "created_at": r["created_at"],
        }

    def get_instance_by_adapter_id(self, adapter_id: str) -> Optional[Dict]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM instances WHERE adapter_id = ?", (adapter_id,))
        r = cur.fetchone()
        cur.close()
        if not r:
            return None
        return {
            "logical_id": r["logical_id"],
            "adapter": r["adapter"],
            "adapter_id": r["adapter_id"],
            "state": r["state"],
            "spec": json.loads(r["spec_json"] or "{}"),
            "created_at": r["created_at"],
        }

    def list_instances(self, adapter: Optional[str] = None) -> List[Dict]:
        cur = self._conn.cursor()
        if adapter:
            cur.execute("SELECT * FROM instances WHERE adapter = ?", (adapter,))
        else:
            cur.execute("SELECT * FROM instances")
        rows = []
        for r in cur.fetchall():
            rows.append(
                {
                    "logical_id": r["logical_id"],
                    "adapter": r["adapter"],
                    "adapter_id": r["adapter_id"],
                    "state": r["state"],
                    "spec": json.loads(r["spec_json"] or "{}"),
                    "created_at": r["created_at"],
                }
            )
        cur.close()
        return rows

    def list_instances_by_prefix(self, prefix: str) -> List[Dict]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM instances WHERE logical_id LIKE ?", (f"{prefix}%",))
        rows = []
        for r in cur.fetchall():
            rows.append(
                {
                    "logical_id": r["logical_id"],
                    "adapter": r["adapter"],
                    "adapter_id": r["adapter_id"],
                    "state": r["state"],
                    "spec": json.loads(r["spec_json"] or "{}"),
                    "created_at": r["created_at"],
                }
            )
        cur.close()
        return rows

    def count_instances(self, logical_id_prefix: str) -> int:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT COUNT(*) as c FROM instances WHERE logical_id LIKE ?",
            (f"{logical_id_prefix}%",),
        )
        c = cur.fetchone()["c"]
        cur.close()
        return int(c)

    # small action logger
    def log_action(self, action: str, details: Dict):
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO actions(ts, action, details) VALUES (?, ?, ?)",
            (int(time.time()), action, json.dumps(details)),
        )
        cur.close()
