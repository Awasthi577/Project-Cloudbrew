import json
from pathlib import Path
from typing import Dict, Set

CACHE_DIR = Path(".cloudbrew_cache/schema")

def find_resource_schemas(obj: Dict) -> Dict:
    """Recursively find first dict named 'resource_schemas' and return it (or empty)."""
    if not isinstance(obj, dict):
        return {}
    if "resource_schemas" in obj and isinstance(obj["resource_schemas"], dict):
        return obj["resource_schemas"]
    for v in obj.values():
        if isinstance(v, dict):
            found = find_resource_schemas(v)
            if found:
                return found
    return {}

def list_resources_for_provider(schema_path: Path) -> Set[str]:
    txt = schema_path.read_text(encoding="utf-8")
    j = json.loads(txt)
    # top-level structure usually has provider_schemas -> <registry id> -> resource_schemas
    resources = set()
    ps = j.get("provider_schemas", {})
    for _, provider_blob in ps.items():
        rs = find_resource_schemas(provider_blob)
        resources.update(rs.keys())
    return resources

if __name__ == "__main__":
    for f in CACHE_DIR.glob("*-schema.json"):
        names = list_resources_for_provider(f)
        print(f"{f.name}: {len(names)} resources (sample 20):")
        print(sorted(list(names))[:20])
