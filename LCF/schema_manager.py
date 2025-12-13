import json
import os
import subprocess
from typing import Dict, Any

CACHE_FILE = "schema_cache.json"

class SchemaManager:
    def __init__(self, work_dir="."):
        self.work_dir = work_dir
        self.cache_path = os.path.join(work_dir, CACHE_FILE)
        self.schema_cache = self._load_or_build_schema()

    # ------------------------------
    # PUBLIC API
    # ------------------------------
    def get(self, resource_type: str) -> Dict[str, Any]:
        return self.schema_cache.get(resource_type, {
            "blocks": {},
            "attributes": {}
        })

    # ------------------------------
    # INTERNALS
    # ------------------------------
    def _load_or_build_schema(self):
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                pass

        return self._fetch_and_parse_schema()

    def _fetch_and_parse_schema(self):
        print("[INFO] Fetching OpenTofu schema (Deep Parse)...")

        # Ensure providers are downloaded
        if not os.path.exists(os.path.join(self.work_dir, ".terraform")):
            subprocess.run(["tofu", "init"], cwd=self.work_dir, check=True, capture_output=True)

        # Fetch the schema
        try:
            result = subprocess.run(
                ["tofu", "providers", "schema", "-json"],
                cwd=self.work_dir,
                capture_output=True,
                check=True,
                encoding="utf-8",       # <--- CRITICAL FIX FOR WINDOWS
                errors="replace"        # <--- PREVENTS CRASHES ON WEIRD CHARS
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
        parsed = {}

        for pdata in provider_schemas.values():
            for rtype, rdef in pdata.get("resource_schemas", {}).items():
                block = rdef.get("block", {})
                parsed[rtype] = self._parse_block_schema(block)

        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(parsed, f, indent=2)

        return parsed

    # ------------------------------
    # RECURSIVE BLOCK PARSER
    # ------------------------------
    def _parse_block_schema(self, block: Dict[str, Any]) -> Dict[str, Any]:
        schema = {
            "blocks": {},      
            "attributes": {}   
        }

        # 1. Attributes
        for attr_name, attr_def in block.get("attributes", {}).items():
            schema["attributes"][attr_name] = self._parse_type(attr_def.get("type"))

        # 2. Nested Blocks
        for block_name, block_info in block.get("block_types", {}).items():
            schema["blocks"][block_name] = {
                "nesting_mode": block_info.get("nesting_mode", "single"),
                "schema": self._parse_block_schema(block_info.get("block", {}))
            }

        return schema

    # ------------------------------
    # RECURSIVE TYPE PARSER
    # ------------------------------
    def _parse_type(self, t):
        if isinstance(t, str):
            return t

        if isinstance(t, list):
            kind = t[0]  
            if kind in ("map", "list", "set"):
                return {
                    "kind": kind,
                    "element": self._parse_type(t[1])
                }
            if kind == "object":
                return {
                    "kind": "object",
                    "attributes": {
                        k: self._parse_type(v) 
                        for k, v in t[1].items()
                    }
                }
        return "string"
