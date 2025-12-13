from __future__ import annotations

import os
import sqlite3
import json
import subprocess
import time
import difflib
import re
from typing import Optional, Dict, Any, List, Tuple

CACHE_DIR = ".cloudbrew_cache"
CACHE_DB = os.path.join(CACHE_DIR, "resources.db")
# Replaced terraform with opentofu
DEFAULT_PROVIDERS = ("opentofu", "pulumi", "aws", "gcp", "azure", "noop")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS resource_schemas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT,
    resource_type TEXT,
    schema_json TEXT,
    fetched_at INTEGER,
    UNIQUE(provider, resource_type)
);
CREATE TABLE IF NOT EXISTS provider_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT,
    resource_name TEXT,
    fetched_at INTEGER,
    UNIQUE(provider, resource_name)
);
"""

# Tunables
_MATCH_THRESHOLD = 0.0
_MAX_CANDIDATES = 8
_SCHEMA_QUERY_TIMEOUT = 30


class ResourceResolver:
    def __init__(self, db_path: Optional[str] = None):
        os.makedirs(CACHE_DIR, exist_ok=True)
        self.db_path = db_path or CACHE_DB
        try:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            cur = self.conn.cursor()
            cur.executescript(SCHEMA_SQL)
            cur.close()
        except Exception:
            self.conn = None
        self._provider_name_cache: Dict[str, List[str]] = {}
        self._last_fetched: Dict[str, int] = {}

    # ----------------------------
    # Low-level schema queries
    # ----------------------------
    def _run_cmd(
        self, cmd: List[str], cwd: Optional[str] = None, timeout: int = _SCHEMA_QUERY_TIMEOUT
    ) -> Tuple[int, str, str]:
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except FileNotFoundError:
            return 127, "", f"executable not found: {cmd[0]!r}"
        except subprocess.TimeoutExpired as e:
            return 124, "", f"timeout: {e}"
        except Exception as e:
            return 1, "", f"error running command: {e}"

    def _query_opentofu_schema(self) -> Dict[str, Any]:
        """
        Queries 'tofu providers schema -json' to discover available resources.
        """
        # Look for 'tofu' first, fallback to 'opentofu' if needed
        binary = "tofu"
        
        rc, out, err = self._run_cmd(
            [binary, "providers", "schema", "-json"], timeout=_SCHEMA_QUERY_TIMEOUT
        )
        if rc == 127:
            raise RuntimeError(f"{binary} CLI not found on PATH (needed for schema discovery).")
        if rc != 0:
            stderr = err.strip() if err else "<no stderr>"
            raise RuntimeError(f"{binary} providers schema failed (exit {rc}): {stderr}")
        try:
            return json.loads(out)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to parse {binary} schema JSON: {e}; stdout={out!r}")

    def _query_pulumi_schema(self) -> Dict[str, Any]:
        rc, out, err = self._run_cmd(["pulumi", "schema", "export"], timeout=_SCHEMA_QUERY_TIMEOUT)
        if rc != 0:
            return {}
        try:
            return json.loads(out)
        except Exception:
            return {}

    # ----------------------------
    # Helpers: tokenization + scoring
    # ----------------------------
    def _tokenize(self, s: str) -> List[str]:
        if not isinstance(s, str):
            return []
        s2 = re.sub(r"[^0-9A-Za-z]+", "_", s)
        parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+", s2)
        toks: List[str] = []
        for p in parts:
            for t in p.split("_"):
                if t:
                    toks.append(t.lower())
        return toks

    def _score_candidate(self, query_tok: List[str], candidate: str) -> float:
        if not candidate:
            return 0.0
        cand_tok = self._tokenize(candidate)
        if not cand_tok:
            return 0.0

        set_q = set(query_tok)
        set_c = set(cand_tok)
        overlap = len(set_q & set_c) / max(len(set_q), 1)

        sub_boost = 0.0
        qjoined = " ".join(query_tok)
        cand_lower = " ".join(cand_tok)
        if any(q in cand_lower for q in query_tok):
            sub_boost = 0.20

        ratio = difflib.SequenceMatcher(a=qjoined, b=cand_lower).ratio()
        score = 0.5 * overlap + 0.3 * ratio + sub_boost
        return max(0.0, min(1.0, score))

    # ----------------------------
    # Provider -> resource names extraction & caching
    # ----------------------------
    def _persist_provider_names(self, provider: str, names: List[str]) -> None:
        if not self.conn or not names:
            return
        try:
            cur = self.conn.cursor()
            now = int(time.time())
            for n in names:
                try:
                    cur.execute(
                        "INSERT OR IGNORE INTO provider_index(provider, resource_name, fetched_at) VALUES (?, ?, ?)",
                        (provider, n, now),
                    )
                except Exception:
                    pass
            self.conn.commit()
            cur.close()
        except Exception:
            pass

    def _load_persisted_provider_names(self, provider: str) -> List[str]:
        if not self.conn:
            return []
        try:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT resource_name FROM provider_index WHERE provider = ? ORDER BY fetched_at DESC",
                (provider,),
            )
            rows = [r[0] for r in cur.fetchall()]
            cur.close()
            return rows
        except Exception:
            return []

    def _gather_provider_resource_names(self, provider: str) -> List[str]:
        prov = (provider or "").lower()
        if prov in self._provider_name_cache:
            return self._provider_name_cache[prov]

        persisted = self._load_persisted_provider_names(prov)
        if persisted:
            self._provider_name_cache[prov] = persisted
            return persisted

        names: List[str] = []
        try:
            if prov in ("opentofu", "tofu"):
                tf = self._query_opentofu_schema()
                if isinstance(tf, dict):
                    provider_schemas = tf.get("provider_schemas", {}) or {}
                    for _, pval in provider_schemas.items():
                        if isinstance(pval, dict):
                            rs = pval.get("resource_schemas") or {}
                            if isinstance(rs, dict):
                                names.extend(rs.keys())
                    if not names and isinstance(tf.get("resource_schemas"), dict):
                        names.extend(tf["resource_schemas"].keys())
            elif prov == "pulumi":
                pul = self._query_pulumi_schema()
                if isinstance(pul, dict):
                    for modval in pul.values():
                        if isinstance(modval, dict) and "resources" in modval:
                            if isinstance(modval["resources"], dict):
                                names.extend(modval["resources"].keys())
            else:
                # fallback: try opentofu anyway (common providers might be visible via tofu)
                try:
                    tf = self._query_opentofu_schema()
                    if isinstance(tf, dict):
                        for _, pval in (tf.get("provider_schemas", {}) or {}).items():
                            rs = pval.get("resource_schemas") or {}
                            if isinstance(rs, dict):
                                names.extend(rs.keys())
                except Exception:
                    pass
        except Exception:
            names = names or []

        uniq = list(dict.fromkeys([n for n in names if isinstance(n, str)]))
        if uniq:
            self._provider_name_cache[prov] = uniq
            self._persist_provider_names(prov, uniq)
        else:
            self._provider_name_cache[prov] = self._load_persisted_provider_names(prov) or []
        return self._provider_name_cache[prov]

    # ----------------------------
    # Discovery: ranking & match selection
    # ----------------------------
    def _discover_best_match(
        self, provider: str, resource_short: str
    ) -> Tuple[float, List[Tuple[str, float]]]:
        prov = (provider or "").lower()
        query_tok = self._tokenize(resource_short)
        if not query_tok:
            return 0.0, []
        candidates = self._gather_provider_resource_names(prov)
        scored = [(cand, self._score_candidate(query_tok, cand)) for cand in candidates]
        scored.sort(key=lambda x: x[1], reverse=True)
        return (scored[0][1] if scored else 0.0, scored[:_MAX_CANDIDATES])

    # ----------------------------
    # Normalization of results
    # ----------------------------
    def _normalize_result(
        self, chosen: str, provider: str, payload: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        base = {"_resolved": chosen, "_provider": provider}
        if isinstance(payload, dict):
            base.update(payload)
        return base

    # ----------------------------
    # Public resolve API
    # ----------------------------
    def resolve(self, *args, **kwargs) -> Dict[str, Any]:
        provider, resource = None, None

        if "resource" in kwargs and "provider" in kwargs:
            resource, provider = kwargs["resource"], kwargs["provider"]
        elif len(args) == 2:
            a0, a1 = args
            if isinstance(a0, str) and a0.lower() in DEFAULT_PROVIDERS:
                provider, resource = a0, a1
            elif isinstance(a1, str) and a1.lower() in DEFAULT_PROVIDERS:
                resource, provider = a0, a1
            else:
                resource, provider = a0, a1
        else:
            raise ValueError(f"Unsupported call signature: args={args}, kwargs={kwargs}")

        provider = (provider or "").lower()
        resource = (resource or "").lower()

        # Validate provider explicitly (allow 'opentofu'/'tofu', disallow 'terraform' unless in default list which we removed)
        if provider and provider != "auto" and provider not in DEFAULT_PROVIDERS:
            # Check if user passed tofu explicitly
            if provider not in ("tofu", "opentofu"):
                 raise ValueError(f"Unsupported provider: {provider}")

        providers_to_try = (
            [provider] if provider and provider != "auto" else list(DEFAULT_PROVIDERS)
        )

        best_overall: Tuple[float, Optional[str], List[Tuple[str, float]]] = (0.0, None, [])
        for p in providers_to_try:
            try:
                score, top = self._discover_best_match(p, resource)
            except Exception:
                score, top = 0.0, []
            if score > best_overall[0]:
                best_overall = (score, p, top)

        best_score, best_provider, best_list = best_overall
        if best_provider and best_score >= _MATCH_THRESHOLD and best_list:
            chosen = best_list[0][0]
            try:
                if best_provider in ("opentofu", "tofu"):
                    tf = self._query_opentofu_schema()

                    def _find(obj, key):
                        if isinstance(obj, dict):
                            if key in obj:
                                return obj[key]
                            for v in obj.values():
                                res = _find(v, key)
                                if res is not None:
                                    return res
                        elif isinstance(obj, list):
                            for it in obj:
                                res = _find(it, key)
                                if res is not None:
                                    return res
                        return None

                    schema_payload = _find(tf, chosen)
                    return self._normalize_result(chosen, best_provider, schema_payload)

                elif best_provider == "pulumi":
                    pul = self._query_pulumi_schema()

                    def _find(obj, key):
                        if isinstance(obj, dict):
                            if key in obj:
                                return obj[key]
                            for v in obj.values():
                                res = _find(v, key)
                                if res is not None:
                                    return res
                        elif isinstance(obj, list):
                            for it in obj:
                                res = _find(it, key)
                                if res is not None:
                                    return res
                        return None

                    schema_payload = _find(pul, chosen)
                    return self._normalize_result(chosen, best_provider, schema_payload)

                else:
                    return {"_resolved": chosen, "_provider": best_provider}

            except Exception as e:
                return {
                    "_resolved": chosen,
                    "_provider": best_provider,
                    "_note": f"schema fetch error: {e}",
                }

        # No match → structured candidate info
        candidate_info = [{"name": n, "score": s} for n, s in (best_list or [])]
        return {
            "message": f"No clear mapping for '{resource}'",
            "resource": resource,
            "tried_providers": providers_to_try,
            "top_candidates": candidate_info,
            "best_score": best_score,
        }