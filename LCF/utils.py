import json
import uuid
from pathlib import Path
from typing import Dict
import subprocess
import os
from typing import Dict, Tuple

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

def ensure_ssh_key(base_name: str, unique: bool = True) -> Tuple[str, str]:
    keys_dir = Path(".cloudbrew_keys")
    keys_dir.mkdir(exist_ok=True)
    
    # Generate a unique suffix to prevent collisions
    suffix = f"-{uuid.uuid4().hex[:8]}" if unique else ""
    key_name = f"{base_name}{suffix}"
    
    private_key_path = keys_dir / key_name
    public_key_path = keys_dir / f"{key_name}.pub"
    
    # Generate only if missing
    if not private_key_path.exists():
        cmd = [
            "ssh-keygen", "-t", "rsa", "-b", "4096", 
            "-f", str(private_key_path), "-N", ""
        ]
        # Allow errors to raise so you know if it fails
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    return public_key_path.read_text(), str(private_key_path)