import os
import tempfile
import shutil
import pytest
import sqlite3

@pytest.fixture
def tmp_db_path(tmp_path):
    return str(tmp_path / "test_resources.db")

@pytest.fixture
def tmp_offload_db(tmp_path):
    return str(tmp_path / "test_offload.db")

@pytest.fixture(autouse=True)
def ensure_env_clean(monkeypatch):
    # ensure tests start clean; you can set more env defaults here
    monkeypatch.delenv("CLOUDBREW_TERRAFORM_BIN", raising=False)
    monkeypatch.delenv("PULUMI_HOME", raising=False)
    yield
