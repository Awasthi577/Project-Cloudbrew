import hashlib
import json
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List

CACHE_FILE = "schema_cache.json"
CACHE_VERSION = "v2"


class SchemaManager:
    def __init__(self, work_dir=".", tofu_bin: str | None = None):
        self.work_dir = work_dir
        self.tofu_bin = (
            tofu_bin
            or os.environ.get("CLOUDBREW_OPENTOFU_BIN")
            or shutil.which("tofu")
            or shutil.which("opentofu")
        )
        self.cache_path = os.path.join(work_dir, CACHE_FILE)
        os.makedirs(self.work_dir, exist_ok=True)
        self.cache_key = self._build_cache_key()
        self.schema_cache = self._load_or_build_schema()

    # ------------------------------
    # PUBLIC API
    # ------------------------------
    def get(self, resource_type: str) -> Dict[str, Any]:
        """Backward-compatible alias for resource schema lookup."""
        return self.get_resource_schema(resource_type)

    def get_resource_schema(self, resource_type: str) -> Dict[str, Any]:
        return self.schema_cache.get(
            resource_type,
            {
                "kind": "block",
                "attributes": {},
                "blocks": {},
            },
        )

    def list_required_paths(self, resource_type: str) -> List[str]:
        schema = self.get_resource_schema(resource_type)
        required_paths: List[str] = []
        self._collect_required_paths(schema, prefix="", out=required_paths)
        return sorted(set(required_paths))

    def list_constraint_rules(self, resource_type: str) -> List[Dict[str, Any]]:
        schema = self.get_resource_schema(resource_type)
        rules: List[Dict[str, Any]] = []
        self._collect_constraint_rules(schema, prefix="", out=rules)
        return rules

    # ------------------------------
    # INTERNALS
    # ------------------------------
    def _load_or_build_schema(self) -> Dict[str, Any]:
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                if self._is_legacy_cache(cached):
                    return cached
                if cached.get("cache_key") == self.cache_key:
                    return cached.get("resource_schemas", {})
            except Exception:
                pass

        return self._fetch_and_parse_schema()

    def _is_legacy_cache(self, cached: Dict[str, Any]) -> bool:
        return isinstance(cached, dict) and "cache_key" not in cached

    def _fetch_and_parse_schema(self) -> Dict[str, Any]:
        print("[INFO] Fetching OpenTofu schema (Deep Parse)...")

        if not self.tofu_bin:
            print("[WARN] OpenTofu binary not found; schema introspection skipped.")
            return {}

        if not os.path.exists(os.path.join(self.work_dir, ".terraform")):
            subprocess.run([self.tofu_bin, "init"], cwd=self.work_dir, check=True, capture_output=True)

        try:
            result = subprocess.run(
                [self.tofu_bin, "providers", "schema", "-json"],
                cwd=self.work_dir,
                capture_output=True,
                check=True,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Failed to fetch schema: {e.stderr}")
            return {}

        try:
            raw = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            print(f"[ERROR] JSON Decode Failed: {e}")
            return {}

        provider_schemas = raw.get("provider_schemas", {})
        parsed: Dict[str, Any] = {}

        for pdata in provider_schemas.values():
            for rtype, rdef in pdata.get("resource_schemas", {}).items():
                block = rdef.get("block", {})
                parsed[rtype] = self._parse_block_schema(block)

        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "cache_version": CACHE_VERSION,
                    "cache_key": self.cache_key,
                    "resource_schemas": parsed,
                },
                f,
                indent=2,
            )

        return parsed

    def _build_cache_key(self) -> str:
        lock_context = {
            "terraform_lock": self._read_lock_file(".terraform.lock.hcl"),
            "tofu_lock": self._read_lock_file(".tofu.lock.hcl"),
            "providers_lock": self._read_lock_file("tofu.lock.hcl"),
        }
        provider_versions = self._extract_provider_versions(lock_context)

        key_material = {
            "cache_version": CACHE_VERSION,
            "tofu_bin": self.tofu_bin or "",
            "lock_context_hash": hashlib.sha256(
                json.dumps(lock_context, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "provider_versions": provider_versions,
        }
        return hashlib.sha256(json.dumps(key_material, sort_keys=True).encode("utf-8")).hexdigest()

    def _read_lock_file(self, file_name: str) -> Dict[str, Any]:
        path = os.path.join(self.work_dir, file_name)
        if not os.path.exists(path):
            return {"exists": False, "file": file_name}

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        return {
            "exists": True,
            "file": file_name,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "content": content,
        }

    def _extract_provider_versions(self, lock_context: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
        provider_versions: List[Dict[str, str]] = []
        for lock in lock_context.values():
            if not lock.get("exists"):
                continue
            content = lock.get("content", "")
            providers = re.findall(
                r'provider\s+"([^"]+)"\s*\{[^}]*?version\s*=\s*"([^"]+)"',
                content,
                flags=re.DOTALL,
            )
            for source, version in providers:
                provider_versions.append({"source": source, "version": version})

        # deterministic order + de-duplication
        unique = {(p["source"], p["version"]) for p in provider_versions}
        return [
            {"source": source, "version": version}
            for source, version in sorted(unique, key=lambda item: (item[0], item[1]))
        ]

    # ------------------------------
    # RECURSIVE BLOCK PARSER
    # ------------------------------
    def _parse_block_schema(self, block: Dict[str, Any]) -> Dict[str, Any]:
        schema = {
            "kind": "block",
            "attributes": {},
            "blocks": {},
        }

        for attr_name, attr_def in block.get("attributes", {}).items():
            schema["attributes"][attr_name] = {
                "required": bool(attr_def.get("required", False)),
                "optional": bool(attr_def.get("optional", False)),
                "computed": bool(attr_def.get("computed", False)),
                "sensitive": bool(attr_def.get("sensitive", False)),
                "type": self._parse_type(attr_def.get("type")),
            }

        for block_name, block_info in block.get("block_types", {}).items():
            schema["blocks"][block_name] = {
                "required": bool(block_info.get("required", False)),
                "optional": bool(block_info.get("optional", False)),
                "computed": bool(block_info.get("computed", False)),
                "sensitive": bool(block_info.get("sensitive", False)),
                "nesting_mode": block_info.get("nesting_mode", "single"),
                "min_items": block_info.get("min_items"),
                "max_items": block_info.get("max_items"),
                "schema": self._parse_block_schema(block_info.get("block", {})),
            }

        return schema

    def _collect_required_paths(self, schema: Dict[str, Any], prefix: str, out: List[str]) -> None:
        for attr_name, attr_def in schema.get("attributes", {}).items():
            if attr_def.get("required"):
                out.append(f"{prefix}{attr_name}")

        for block_name, block_def in schema.get("blocks", {}).items():
            block_prefix = f"{prefix}{block_name}"
            if block_def.get("required") or (block_def.get("min_items") or 0) > 0:
                out.append(block_prefix)
            self._collect_required_paths(block_def.get("schema", {}), f"{block_prefix}.", out)

    def _collect_constraint_rules(self, schema: Dict[str, Any], prefix: str, out: List[Dict[str, Any]]) -> None:
        for attr_name, attr_def in schema.get("attributes", {}).items():
            path = f"{prefix}{attr_name}"
            flags = {k: bool(attr_def.get(k, False)) for k in ["required", "optional", "computed", "sensitive"]}
            out.append({"path": path, "kind": "attribute", "flags": flags, "type": attr_def.get("type")})

        for block_name, block_def in schema.get("blocks", {}).items():
            path = f"{prefix}{block_name}"
            out.append(
                {
                    "path": path,
                    "kind": "block",
                    "flags": {
                        "required": bool(block_def.get("required", False)),
                        "optional": bool(block_def.get("optional", False)),
                        "computed": bool(block_def.get("computed", False)),
                        "sensitive": bool(block_def.get("sensitive", False)),
                    },
                    "nesting_mode": block_def.get("nesting_mode"),
                    "min_items": block_def.get("min_items"),
                    "max_items": block_def.get("max_items"),
                }
            )
            self._collect_constraint_rules(block_def.get("schema", {}), f"{path}.", out)

    # ------------------------------
    # RECURSIVE TYPE PARSER
    # ------------------------------
    def _parse_type(self, t: Any) -> Any:
        if isinstance(t, str):
            return {"kind": "primitive", "name": t}

        if isinstance(t, list) and t:
            kind = t[0]
            if kind in ("map", "list", "set") and len(t) > 1:
                return {
                    "kind": kind,
                    "element": self._parse_type(t[1]),
                }
            if kind == "tuple" and len(t) > 1:
                return {
                    "kind": "tuple",
                    "elements": [self._parse_type(v) for v in t[1]],
                }
            if kind == "object" and len(t) > 1 and isinstance(t[1], dict):
                return {
                    "kind": "object",
                    "attributes": {k: self._parse_type(v) for k, v in t[1].items()},
                }

        return {"kind": "primitive", "name": "string"}
