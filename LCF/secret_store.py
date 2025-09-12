# cloudbrew/secret_store.py
from __future__ import annotations
import base64
import pathlib
import os
from typing import Optional, Dict

try:
    import keyring
except Exception:
    keyring = None

try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception:
    Fernet = None

CONFIG_DIR = pathlib.Path.home() / ".cloudbrew"
CONFIG_DIR.mkdir(mode=0o700, exist_ok=True)

FERNET_KEY_NAME = "cloudbrew_fernet_key"


class SecretStore:
    """Abstraction over keyring (primary) and Fernet-file (fallback).

    Methods:
      - store_secret(key, secret) -> meta
      - retrieve_secret(key) -> secret or None
      - secret_exists(key) -> bool
    """

    def __init__(self):
        self._has_keyring = keyring is not None
        self._has_fernet = Fernet is not None

    def _fernet_key_path(self) -> pathlib.Path:
        return CONFIG_DIR / f"{FERNET_KEY_NAME}.key"

    def _ensure_fernet_key(self) -> bytes:
        path = self._fernet_key_path()
        if path.exists():
            return path.read_bytes()
        # generate
        key = Fernet.generate_key()
        path.write_bytes(key)
        os.chmod(path, 0o600)
        return key

    def store_secret(self, key: str, secret: str) -> Dict:
        """Store secret and return metadata describing storage method."""
        if self._has_keyring:
            keyring.set_password("cloudbrew", key, secret)
            return {"method": "keyring", "key": key}

        if self._has_fernet:
            fkey = self._ensure_fernet_key()
            f = Fernet(fkey)
            token = f.encrypt(secret.encode("utf-8"))
            path = CONFIG_DIR / f"{key}.secret"
            path.write_bytes(token)
            os.chmod(path, 0o600)
            return {"method": "fernet-file", "path": str(path)}

        # last resort: store base64 encoded (not recommended) with restricted perms
        path = CONFIG_DIR / f"{key}.secret.b64"
        path.write_bytes(base64.b64encode(secret.encode("utf-8")))
        os.chmod(path, 0o600)
        return {"method": "file-b64", "path": str(path)}

    def retrieve_secret(self, key: str) -> Optional[str]:
        if self._has_keyring:
            val = keyring.get_password("cloudbrew", key)
            return val

        if self._has_fernet:
            path = CONFIG_DIR / f"{key}.secret"
            if not path.exists():
                return None
            try:
                fkey = self._ensure_fernet_key()
                f = Fernet(fkey)
                token = path.read_bytes()
                return f.decrypt(token).decode("utf-8")
            except InvalidToken:
                return None

        path = CONFIG_DIR / f"{key}.secret.b64"
        if path.exists():
            return base64.b64decode(path.read_bytes()).decode("utf-8")
        return None

    def secret_exists(self, key: str) -> bool:
        if self._has_keyring:
            return keyring.get_password("cloudbrew", key) is not None
        if self._has_fernet:
            return (CONFIG_DIR / f"{key}.secret").exists()
        return (CONFIG_DIR / f"{key}.secret.b64").exists()
