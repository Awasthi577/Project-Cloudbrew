"""Compatibility layer for legacy secret manager imports.

This module keeps older import paths working while delegating to SecretStore.
"""

from __future__ import annotations

from typing import Optional, Dict

from LCF.secret_store import SecretStore


class SecretManager:
    """Backward-compatible facade over :class:`LCF.secret_store.SecretStore`."""

    def __init__(self):
        self._store = SecretStore()

    def store_secret(self, key: str, secret: str) -> Dict:
        return self._store.store_secret(key, secret)

    def retrieve_secret(self, key: str) -> Optional[str]:
        return self._store.retrieve_secret(key)

    def secret_exists(self, key: str) -> bool:
        return self._store.secret_exists(key)
