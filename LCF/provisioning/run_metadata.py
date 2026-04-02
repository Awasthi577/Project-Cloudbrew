from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunRecord:
    run_id: str
    created_at: str
    payload: Dict[str, Any]


class RunMetadataStore:
    """Persists provisioning run metadata in SQLite (default) or JSON file."""

    def __init__(self, storage_path: str = ".cloudbrew/runs.sqlite") -> None:
        self.path = Path(storage_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.mode = "json" if self.path.suffix.lower() == ".json" else "sqlite"
        if self.mode == "sqlite":
            self._init_sqlite()
        else:
            self._init_json()

    def _init_sqlite(self) -> None:
        with sqlite3.connect(str(self.path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def _init_json(self) -> None:
        if not self.path.exists():
            self.path.write_text(json.dumps({"runs": {}}, indent=2), encoding="utf-8")

    def _new_run_id(self) -> str:
        return f"run-{uuid.uuid4().hex[:12]}"

    def save(self, payload: Dict[str, Any], run_id: Optional[str] = None) -> str:
        rid = run_id or self._new_run_id()
        created_at = _utc_now_iso()
        if self.mode == "sqlite":
            with sqlite3.connect(str(self.path)) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO runs(run_id, created_at, payload_json)
                    VALUES (?, ?, ?)
                    """,
                    (rid, created_at, json.dumps(payload)),
                )
                conn.commit()
        else:
            data = self._read_json_doc()
            data.setdefault("runs", {})[rid] = {"created_at": created_at, "payload": payload}
            self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return rid

    def get(self, run_id: str) -> Optional[RunRecord]:
        if self.mode == "sqlite":
            with sqlite3.connect(str(self.path)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT run_id, created_at, payload_json FROM runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
            if not row:
                return None
            return RunRecord(run_id=row["run_id"], created_at=row["created_at"], payload=json.loads(row["payload_json"]))

        data = self._read_json_doc()
        rec = (data.get("runs") or {}).get(run_id)
        if not rec:
            return None
        return RunRecord(run_id=run_id, created_at=rec.get("created_at", ""), payload=rec.get("payload", {}))

    def _read_json_doc(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"runs": {}}
        raw = self.path.read_text(encoding="utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"runs": {}}
