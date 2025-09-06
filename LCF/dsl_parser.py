import yaml
from typing import Dict

def parse_spec(path: str) -> Dict:
    """
    Very small YAML parser. Returns dict for spec.
    """
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("Spec must be a YAML mapping")
    return data
