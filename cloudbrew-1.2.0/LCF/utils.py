import json
from pathlib import Path
from typing import Dict

STATE_FILE = Path(".cloudbrew_state.json")


def save_state(state: Dict):
    existing = {}
    if STATE_FILE.exists():
        try:
            existing = json.loads(STATE_FILE.read_text())
        except Exception:
            existing = {}
    existing.update(state)
    STATE_FILE.write_text(json.dumps(existing, indent=2))


def load_state() -> Dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def some_helper(x: int) -> int:
    """
    Dummy helper function to satisfy test stubs.
    Currently doubles the input value.
    Replace or remove once tests are aligned with real utils.
    """
    return x * 2
