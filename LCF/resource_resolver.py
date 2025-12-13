from __future__ import annotations
import shutil
import os
import sqlite3
import json
import subprocess
import time
import difflib
import re
from functools import lru_cache
from typing import Optional, Dict, Any, List, Tuple

# Try importing SchemaManager; handle case if LCF module is missing to avoid immediate crash
try:
    from LCF.schema_manager import SchemaManager
except ImportError:
    SchemaManager = None

# ------------------------------------------------------
# CONSTANTS & CACHE
# ------------------------------------------------------
CACHE_DIR = ".cloudbrew_cache"
SCHEMA_DIR = os.path.join(CACHE_DIR, "schema_gen")
CACHE_DB = os.path.join(CACHE_DIR, "resources.db")
MAPPINGS_DIR = os.path.join(os.path.dirname(__file__), "mappings")
DEFAULT_PROVIDERS = ("opentofu", "pulumi", "aws", "gcp", "azure", "noop")

# Added Constants
_SCHEMA_CACHE_TTL = int(os.environ.get("CLOUDBREW_SCHEMA_CACHE_TTL", "3600"))  # seconds
_TOFU_ROOT = os.environ.get("CLOUDBREW_TOFU_ROOT", ".cloudbrew_tofu")

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

_MATCH_THRESHOLD = 0.3
_MAX_CANDIDATES = 8
_SCHEMA_QUERY_TIMEOUT = 30
MAPPINGS_DIR = os.path.join(os.path.dirname(__file__), "mappings")


# ======================================================================
# RESOURCE RESOLVER (COMPLETE REWRITE WITH STRICT AZURE OVERRIDE)
# ======================================================================

class ResourceResolver:

    # ------------------------------------------------------
    # STRICT (C1) — EXACT MATCH Azure mapping
    # ------------------------------------------------------
    AZURE_STATIC_MAP = {
        "vm": "azurerm_linux_virtual_machine",
        "virtual_machine": "azurerm_linux_virtual_machine",
        "winvm": "azurerm_windows_virtual_machine",
        "windows_vm": "azurerm_windows_virtual_machine",
        "resource_group": "azurerm_resource_group",
        "rg": "azurerm_resource_group",
        "vnet": "azurerm_virtual_network",
        "subnet": "azurerm_subnet",
        "storage": "azurerm_storage_account",
        "storage_account": "azurerm_storage_account",
    }

    # ------------------------------------------------------
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
        self.static_registry = {}
        self._load_static_mappings()
        self.tofu_binary = self._find_binary()

    def _find_binary(self) -> str:
        return os.environ.get("CLOUDBREW_OPENTOFU_BIN") or shutil.which("tofu") or shutil.which("opentofu") or "tofu"
    
    def _load_static_mappings(self):
        """Loads all JSON files from the mappings/ directory."""
        if not os.path.exists(MAPPINGS_DIR):
            return

        for filename in os.listdir(MAPPINGS_DIR):
            if filename.endswith(".json"):
                path = os.path.join(MAPPINGS_DIR, filename)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        # FIX: Append to list instead of overwriting
                        for key, spec in data.items():
                            if key not in self.static_registry:
                                self.static_registry[key] = []
                            # Store as a list of specs
                            self.static_registry[key].append(spec)
                except Exception as e:
                    print(f"Warning: Failed to load mapping {filename}: {e}")

    def _format_success(self, alias: str, spec: Dict) -> Dict[str, Any]:
        return {
            "_resolved": spec.get("type"),
            "_provider": spec.get("provider"),
            "_defaults": spec.get("defaults", {}),
            "_required": spec.get("required", [])
        }

    # ======================================================================
    # INTERNAL SYSTEM COMMAND RUNNER
    # ======================================================================
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
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            return proc.returncode, proc.stdout, proc.stderr

        except FileNotFoundError:
            return 127, "", f"executable not found: {cmd[0]!r}"

        except subprocess.TimeoutExpired as e:
            return 124, "", f"timeout: {e}"

        except Exception as e:
            return 1, "", f"error running command: {e}"

    # ======================================================================
    # OPENTOFU SCHEMA FETCH
    # ======================================================================
    def _bootstrap_provider(self, provider: str) -> Optional[str]:
        """
        Dynamically installs a provider in an isolated directory so we can query its schema.
        Safe: Does not touch project .terraform folder.
        """
        provider = self._normalize_provider(provider)
        if provider in ("noop", "unknown", "auto", "opentofu", "tofu"):
            return None

        # Map simplified names to official registry sources
        sources = {
            "aws": "hashicorp/aws",
            "google": "hashicorp/google",
            "azurerm": "hashicorp/azurerm",
            "azure": "hashicorp/azurerm",
            "kubernetes": "hashicorp/kubernetes",
            "helm": "hashicorp/helm"
        }
        
        source = sources.get(provider, f"hashicorp/{provider}")
        work_dir = os.path.join(SCHEMA_DIR, provider)
        os.makedirs(work_dir, exist_ok=True)

        # 1. Create a dummy main.tf forcing the provider download
        tf_file = os.path.join(work_dir, "main.tf")
        if not os.path.exists(tf_file):
            with open(tf_file, "w") as f:
                f.write(f"""
terraform {{
  required_providers {{
    {provider} = {{
      source = "{source}"
    }}
  }}
}}
provider "{provider}" {{}}
""")

        # 2. Run Init if .terraform is missing (Optimized check)
        if not os.path.exists(os.path.join(work_dir, ".terraform")):
            self._run_cmd(["tofu", "init", "-no-color"], cwd=work_dir)
            
        return work_dir

    def _query_opentofu_schema(self, provider: str = None) -> Dict[str, Any]:
        binary = "tofu"
        cwd = None

        # 1. Try to bootstrap specific provider (Fixes 'missing schema' error)
        if provider:
            cwd = self._bootstrap_provider(provider)
        elif not provider:
             # Fallback: maintain your existing azure hack if no provider specified
             self._ensure_azure_provider_installed()

        # 2. Run the schema command
        rc, out, err = self._run_cmd(
            [self.tofu_binary, "providers", "schema", "-json"],
            cwd=cwd, # Important: Run inside the folder that has the plugin
            timeout=_SCHEMA_QUERY_TIMEOUT,
        )

        if rc != 0:
            return {}

        try:
            return json.loads(out)
        except Exception:
            return {}

    # ======================================================================
    # TOKENIZER + MATCH SCORING
    # ======================================================================
    def _tokenize(self, s: str) -> List[str]:
        if not isinstance(s, str):
            return []
        s2 = re.sub(r"[^0-9A-Za-z]+", "_", s)
        parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+", s2)

        tokens: List[str] = []
        for p in parts:
            for t in p.split("_"):
                if t:
                    tokens.append(t.lower())
        return tokens

    def _score_candidate_tokens(self, query_tok: List[str], candidate: str) -> float:
        """Original token scoring for simple list matches (used by _discover_best_match)"""
        if not candidate: return 0.0
        cand_tok = self._tokenize(candidate)
        if not cand_tok: return 0.0

        # 1. Identify "Important" words (len > 3, e.g. "cosmosdb", "dynamodb")
        important_keywords = {t for t in query_tok if len(t) > 3}
        
        # 2. Check if candidate contains them
        matches_important = 0
        for kw in important_keywords:
            if any(kw in ct for ct in cand_tok):
                matches_important += 1
        
        if important_keywords and matches_important == 0:
            return 0.0

        # 3. Standard Scoring
        set_q = set(query_tok)
        set_c = set(cand_tok)
        overlap = len(set_q & set_c) / max(len(set_q), 1)
        sub_boost = 0.20 if any(q in " ".join(cand_tok) for q in query_tok) else 0.0
        ratio = difflib.SequenceMatcher(a=" ".join(query_tok), b=" ".join(cand_tok)).ratio()
        
        base_score = 0.5 * overlap + 0.3 * ratio + sub_boost

        # 4. Suffix Penalty
        penalized_suffixes = {"tag", "attachment", "association", "accepter", "policy_attachment", "admin_account"}
        cand_parts = candidate.lower().split("_")
        suffix = cand_parts[-1] if cand_parts else ""
        if suffix in penalized_suffixes and suffix not in query_tok:
            base_score -= 0.25

        return max(0.0, min(1.0, base_score))

    # ======================================================================
    # DB CACHE HELPERS
    # ======================================================================
    def _persist_provider_names(self, provider: str, names: List[str]):
        if not self.conn:
            return
        try:
            now = int(time.time())
            cur = self.conn.cursor()
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
            out = [r[0] for r in cur.fetchall()]
            cur.close()
            return out
        except Exception:
            return []

    # ======================================================================
    # AUTO-INSTALL AzureRM provider folder
    # ======================================================================
    def _ensure_azure_provider_installed(self):
        base_dir = os.path.join(".cloudbrew_providers", "azurerm")
        os.makedirs(base_dir, exist_ok=True)
        versions_tf = os.path.join(base_dir, "versions.tf")
        provider_tf = os.path.join(base_dir, "provider.tf")

        if not os.path.exists(versions_tf):
            with open(versions_tf, "w") as f:
                f.write(
                    """
terraform {
  required_providers {
    azurerm = {
      source  = "registry.opentofu.org/hashicorp/azurerm"
      version = ">= 3.0.0"
    }
  }
}
"""
                )

        if not os.path.exists(provider_tf):
            with open(provider_tf, "w") as f:
                f.write(
                    """
provider "azurerm" {
  features {}
}
"""
                )

        self._run_cmd(["tofu", "init"], cwd=base_dir)

    # ======================================================================
    # GATHER ALL RESOURCE NAMES FROM PROVIDER SCHEMAS
    # ======================================================================
    def _gather_provider_resource_names(self, provider: str) -> List[str]:
        prov = provider.lower()
        if prov == "gcp":
            prov = "google"
        if prov == "azure":
            prov = "azurerm"

        if prov in self._provider_name_cache:
            return self._provider_name_cache[prov]

        persisted = self._load_persisted_provider_names(prov)
        if persisted:
            self._provider_name_cache[prov] = persisted
            return persisted

        names: List[str] = []
        try:
            tf = self._query_opentofu_schema(prov)
            provider_schemas = tf.get("provider_schemas", {}) or {}

            for p_name, p_val in provider_schemas.items():
                if not p_name.endswith(f"/{prov}"):
                    continue
                rs = p_val.get("resource_schemas", {})
                if isinstance(rs, dict):
                    names.extend(rs.keys())

        except Exception:
            names = names or []

        uniq = list(dict.fromkeys(n for n in names if isinstance(n, str)))
        self._provider_name_cache[prov] = uniq
        self._persist_provider_names(prov, uniq)
        return uniq
    
    # ======================================================================
    # DYNAMIC SCHEMA RESOLUTION METHODS
    # ======================================================================
    def _list_provider_resource_types(self, provider: str) -> Tuple[str, dict]:
        """
        Return (provider_id, mapping) where mapping is {resource_type_name: schema_block}
        This wraps SchemaManager; avoid repeated expensive calls by caching within instance.
        """
        if SchemaManager is None:
            return {}

        if not hasattr(self, "_schema_mgr"):
            # reuse same work_dir as other code
            self._schema_mgr = SchemaManager(work_dir=_TOFU_ROOT)

        cache_attr = f"_schema_cache_{provider}"
        cache_time_attr = f"_schema_cache_time_{provider}"

        now = time.time()
        cached = getattr(self, cache_attr, None)
        cached_time = getattr(self, cache_time_attr, 0)
        if cached and (now - cached_time) < _SCHEMA_CACHE_TTL:
            return cached

        try:
            # SchemaManager.get_all_resources() is hypothetical; adapt to whichever API you have.
            # We'll try a few common APIs on SchemaManager; pick the first that exists.
            types = {}
            mgr = self._schema_mgr
            # Preferred API name(s) - adapt if your SchemaManager differs
            if hasattr(mgr, "list_resource_types_for_provider"):
                types_list = mgr.list_resource_types_for_provider(provider)
                # expected to return list of resource names; fetch full block for each
                for rt in types_list:
                    try:
                        schema = mgr.get(rt)
                        types[rt] = schema
                    except Exception:
                        types[rt] = None
            elif hasattr(mgr, "get_provider_schema"):
                prov_schema = mgr.get_provider_schema(provider)
                # prov_schema may be dict of resource types -> definitions
                if isinstance(prov_schema, dict):
                    for rt, schema in prov_schema.items():
                        types[rt] = schema
            else:
                # Fallback: try to call get for a guessed provider prefix (may be slow)
                candidates = ["aws_", "azurerm_", "google_"]
                for prefix in candidates:
                    try:
                        # try to sniff resource names by checking for some well-known resources
                        for well in (f"{prefix}instance", f"{prefix}bucket", f"{prefix}virtual_machine"):
                            try:
                                schema = mgr.get(well)
                                if schema:
                                    types[well] = schema
                            except Exception:
                                pass
                    except Exception:
                        pass

            setattr(self, cache_attr, types)
            setattr(self, cache_time_attr, now)
            return types
        except Exception as e:
            # On any failure, cache empty to avoid tight loop
            setattr(self, cache_attr, {})
            setattr(self, cache_time_attr, now)
            return {}

    def _score_candidate(self, user_word: str, resource_type: str, schema_block: dict) -> float:
        """
        Compute a similarity score between user_word and a provider resource.
        Combines:
          - difflib ratio on names
          - token overlap (split on _ and -)
          - presence of user_word in schema description boosts score
        Returns value in [0,1]
        """
        uw = user_word.lower()
        rt = resource_type.lower()

        # base name ratio
        name_ratio = difflib.SequenceMatcher(None, uw, rt).ratio()

        # token overlap
        uw_tokens = set(re.split(r"[_\-\s]+", uw))
        rt_tokens = set(re.split(r"[_\-\s]+", rt))
        token_overlap = 0.0
        if uw_tokens:
            token_overlap = len(uw_tokens & rt_tokens) / len(uw_tokens)

        score = max(name_ratio, token_overlap)

        # description boost
        try:
            desc = ""
            if schema_block and isinstance(schema_block, dict):
                # schema_block may include 'block' -> 'description', or top-level 'description'
                if "description" in schema_block:
                    desc = str(schema_block.get("description") or "")
                elif "block" in schema_block and isinstance(schema_block["block"], dict):
                    desc = str(schema_block["block"].get("description") or "")
                # also check nested attribute descriptions
                if not desc:
                    attrs = schema_block.get("block", {}).get("attributes", {}) if schema_block.get("block") else {}
                    for a, ad in attrs.items():
                        if isinstance(ad, dict) and "description" in ad:
                            desc += " " + str(ad.get("description") or "")
            if uw in desc.lower():
                score = min(1.0, score + 0.25)
            else:
                # partial token match with description words
                desc_tokens = set(re.findall(r"[a-zA-Z0-9]+", desc.lower()))
                if desc_tokens and uw_tokens & desc_tokens:
                    score = min(1.0, score + 0.15)
        except Exception:
            pass

        # clamp
        if score < 0:
            score = 0.0
        if score > 1:
            score = 1.0
        return float(score)

    def dynamic_resolve_via_schema(self, resource_word: str, provider_hint: str = "auto", top_n: int = 6) -> dict:
        word = (resource_word or "").strip()
        if not word:
            return {"error": "empty resource word"}

        # providers to probe
        providers = []
        if provider_hint and provider_hint.lower() not in ("", "auto", "any"):
            providers = [provider_hint]
        else:
            providers = ["aws", "azurerm", "google"]  # order gives preference; adjust as needed

        all_candidates = []
        for p in providers:
            try:
                types_map = self._list_provider_resource_types(p)
                if not types_map:
                    continue
                for rtype, schema_block in types_map.items():
                    # score
                    score = self._score_candidate(word, rtype, schema_block)
                    if score <= 0.0:
                        continue
                    all_candidates.append((p, rtype, score))
            except Exception:
                continue

        # sort by score desc
        all_candidates.sort(key=lambda x: x[2], reverse=True)

        if not all_candidates:
            return {"error": "no candidates found from provider schemas"}

        # build top candidates list
        top = [{"provider": p, "type": t, "score": s} for p, t, s in all_candidates[:top_n]]

        # decide threshold confidence to auto-pick
        top_provider, top_type, top_score = all_candidates[0]
        # dynamic thresholds:
        # - if top_score >= 0.75: high confidence
        # - if top_score >= 0.60 and second is sufficiently lower -> accept
        if top_score >= 0.75:
            return {"_resolved": top_type, "provider": top_provider, "score": top_score, "top_candidates": top}
        if top_score >= 0.60 and len(all_candidates) > 1 and (top_score - all_candidates[1][2]) >= 0.12:
            return {"_resolved": top_type, "provider": top_provider, "score": top_score, "top_candidates": top}

        # ambiguous: return candidates for interactive selection
        return {"top_candidates": top, "note": "ambiguous_candidates", "query": word}

    # ======================================================================
    # STRICT C1 AZURE OVERRIDE + DEFAULT MATCH LOGIC
    # ======================================================================
    def _discover_best_match(self, provider: str, resource_short: str) -> Tuple[float, List[Tuple[str, float]]]:

        prov = provider.lower()
        key = resource_short.lower()

        # STRICT: if exact match → return immediately
        if prov in ("azure", "azurerm") and key in self.AZURE_STATIC_MAP:
            chosen = self.AZURE_STATIC_MAP[key]
            return 1.0, [(chosen, 1.0)]

        # Otherwise scoring
        tokens = self._tokenize(key)
        if not tokens:
            return 0.0, []

        candidates = self._gather_provider_resource_names(prov)

        # Updated to use _score_candidate_tokens to avoid conflict with new _score_candidate method
        scored = [(c, self._score_candidate_tokens(tokens, c)) for c in candidates]
        scored.sort(key=lambda x: x[1], reverse=True)

        return (scored[0][1] if scored else 0.0, scored[:_MAX_CANDIDATES])

    # ======================================================================
    # NORMALIZE RESULT STRUCTURE
    # ======================================================================
    def _normalize_result(self, chosen: str, provider: str, payload: Optional[Dict[str, Any]]):
        base = {"_resolved": chosen, "_provider": provider}
        if isinstance(payload, dict):
            base.update(payload)
        return base

    # ======================================================================
    # PUBLIC RESOLVE() API — CLEAN + FIXED
    # ======================================================================
    def resolve(self, *args, **kwargs) -> Dict[str, Any]:
    # ----------------------------------------------
    # 0. Normalize Inputs First (Important!)
    # ----------------------------------------------
        provider = kwargs.get("provider", "").lower() or "auto"
        resource = kwargs.get("resource")

    # Normalize provider synonyms BEFORE static lookup
        provider = self._normalize_provider(provider)

    # Parse args
        if not resource and len(args) >= 1:
            resource = args[0]
            if len(args) > 1:
                provider = self._normalize_provider(args[1])

        resource = (resource or "").lower()

    # ----------------------------------------------
    # 1. STATIC LOOKUP (non-blocking)
    # ----------------------------------------------
        if resource in self.static_registry:
            candidates = self.static_registry[resource]
            
            # Safety: ensure it's a list (in case of partial load)
            if not isinstance(candidates, list):
                candidates = [candidates]

            for match in candidates:
                mapped_provider = self._normalize_provider(match.get("provider", ""))

                # If provider matches OR user asked for 'auto', return this match
                if provider == "auto" or provider == mapped_provider:
                    return self._format_success(resource, match) 
    # ----------------------------------------------
    # 2. DYNAMIC LOOKUP
    # ----------------------------------------------
        providers_to_try = [provider]

    # Azure normalization
        if provider in ("azure", "azurerm"):
            providers_to_try = ["azurerm"]

    # GCP normalization
        if provider in ("gcp", "google", "google-native"):
            providers_to_try = ["google"]

        best_score = 0.0
        best_provider = None
        best_list = []

        for p in providers_to_try:
            score, results = self._discover_best_match(p, resource)
            if score > best_score:
                best_score = score
                best_provider = p
                best_list = results
    # ----------------------------------------------
    # 3. Successful dynamic match
    # ----------------------------------------------
        if best_provider and best_list and best_score >= _MATCH_THRESHOLD:
            chosen = best_list[0][0]

            def _seek(obj, key):
                if isinstance(obj, dict):
                    if key in obj: return obj[key]
                    for v in obj.values():
                        r = _seek(v, key)
                        if r is not None: return r
                elif isinstance(obj, list):
                    for item in obj:
                        r = _seek(item, key)
                        if r is not None: return r

            try:
                if best_provider in ("opentofu", "tofu") or best_provider in ("aws", "google", "azurerm"):
                    schema = self._query_opentofu_schema(best_provider)
                    return self._normalize_result(chosen, best_provider, _seek(schema, chosen))

                if best_provider == "pulumi":
                    # Note: _query_pulumi_schema was not defined in the source but is called here
                    # Kept to maintain original logic integrity
                    return {"_resolved": chosen, "_provider": best_provider}

                return {"_resolved": chosen, "_provider": best_provider}

            except Exception as e:
                return {"_resolved": chosen, "_provider": best_provider, "_note": str(e)}

    # ----------------------------------------------
    # 4. No match (return suggestions)
    # ----------------------------------------------
        return {
            "message": f"No clear mapping for '{resource}'",
            "resource": resource,
            "top_candidates": [{"name": n, "score": s} for n, s in best_list],
            "best_score": best_score,
            "tried_providers": providers_to_try,
            "mode": "dynamic_lookup_failed"
        }

    # ======================================================================
    # NORMALIZE PROVIDER NAMES
    # ======================================================================
    def _normalize_provider(self, provider: str) -> str:
        p = provider.lower()

        if p in ("azure", "azurerm", "az"):
            return "azurerm"

        if p in ("aws", "amazon"):
            return "aws"

        if p in ("gcp", "google", "google_cloud"):
            return "google"

        return p