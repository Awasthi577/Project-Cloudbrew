import sqlite3
import time
import json
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Optional

from LCF.cloud_adapters.opentofu_adapter import OpenTofuAdapter

logger = logging.getLogger(__name__)

# Schema for the pool state
POOL_SCHEMA = """
CREATE TABLE IF NOT EXISTS resource_pool (
    id TEXT PRIMARY KEY,           -- UUID for internal tracking
    logical_id TEXT,               -- OpenTofu Resource ID (e.g., pool-hot-1)
    provider TEXT,                 -- aws, azure, etc.
    tier TEXT,                     -- 'hot' or 'warm'
    status TEXT,                   -- 'creating', 'available', 'claimed', 'error'
    spec_hash TEXT,                -- Hash of the spec (size/image) to match requests
    details JSON,                  -- Connection info (IP, etc.)
    created_at INTEGER,
    updated_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_pool_status ON resource_pool(status, tier, spec_hash);
"""

class WarmPoolManager:
    DB_PATH = "cloudbrew_pool.db"
    
    # Configuration for pool targets
    # In a real app, load this from config.json
    TARGETS = {
        "aws:t3.micro:ubuntu-22.04": {
            "hot": 2,   # Keep 2 running
            "warm": 5   # Keep 5 stopped
        }
    }

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()
        self.adapter = OpenTofuAdapter()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(POOL_SCHEMA)

    def _get_connection(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def reconcile(self):
        """
        Main control loop. Checks current pool state vs targets and triggers actions.
        Should be called periodically by a worker.
        """
        logger.info("Starting Warm Pool Reconciliation...")
        
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            for key, targets in self.TARGETS.items():
                provider, size, image = key.split(":")
                spec_hash = key # Simple hash for demo
                
                # Check Hot Tier
                cursor.execute(
                    "SELECT count(*) FROM resource_pool WHERE spec_hash=? AND tier='hot' AND status IN ('available', 'creating')",
                    (spec_hash,)
                )
                current_hot = cursor.fetchone()[0]
                
                if current_hot < targets["hot"]:
                    needed = targets["hot"] - current_hot
                    logger.info(f"Pool '{key}' (Hot): {current_hot}/{targets['hot']}. Provisioning {needed}...")
                    self._trigger_provisioning(needed, "hot", provider, size, image)

                # Check Warm Tier
                cursor.execute(
                    "SELECT count(*) FROM resource_pool WHERE spec_hash=? AND tier='warm' AND status IN ('available', 'creating')",
                    (spec_hash,)
                )
                current_warm = cursor.fetchone()[0]
                
                if current_warm < targets["warm"]:
                    needed = targets["warm"] - current_warm
                    logger.info(f"Pool '{key}' (Warm): {current_warm}/{targets['warm']}. Provisioning {needed}...")
                    self._trigger_provisioning(needed, "warm", provider, size, image)

    def _trigger_provisioning(self, count: int, tier: str, provider: str, size: str, image: str):
        """Spins up background threads to provision resources."""
        with ThreadPoolExecutor(max_workers=count) as executor:
            for _ in range(count):
                executor.submit(self._provision_instance, tier, provider, size, image)

    def _provision_instance(self, tier: str, provider: str, size: str, image: str):
        """
        Actually calls OpenTofu to create the instance.
        """
        pool_id = str(uuid.uuid4())
        logical_name = f"pool-{tier}-{pool_id[:8]}"
        spec_hash = f"{provider}:{size}:{image}"
        
        # 1. Record 'creating' state
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO resource_pool (id, logical_id, provider, tier, status, spec_hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (pool_id, logical_name, provider, tier, 'creating', spec_hash, int(time.time()), int(time.time()))
            )

        logger.info(f"Provisioning {logical_name} ({tier})...")
        
        # 2. Call OpenTofu
        spec = {
            "name": logical_name,
            "type": "vm",
            "provider": provider,
            "size": size,
            "image": image,
            "tags": {"Pool": tier, "ManagedBy": "CloudBrew"}
        }

        try:
            # Plan & Apply
            res = self.adapter.create_instance(logical_name, spec, plan_only=False)
            
            if res.get("success"):
                # If tier is 'warm', we technically should STOP the instance now. 
                # For this demo, we assume 'available' means it exists.
                # In full prod, add a 'stop_instance' call here for L2.
                
                details = {"output": res.get("output", "")}
                
                with self._get_connection() as conn:
                    conn.execute(
                        "UPDATE resource_pool SET status='available', details=?, updated_at=? WHERE id=?",
                        (json.dumps(details), int(time.time()), pool_id)
                    )
                logger.info(f"Instance {logical_name} is now AVAILABLE in {tier} pool.")
            else:
                raise Exception(res.get("error"))

        except Exception as e:
            logger.error(f"Failed to provision {logical_name}: {e}")
            with self._get_connection() as conn:
                conn.execute("UPDATE resource_pool SET status='error' WHERE id=?", (pool_id,))

    def get_available_resource(self, provider: str, size: str, image: str) -> Optional[Dict[str, Any]]:
        """
        Attempts to find and lock an available HOT resource.
        """
        spec_hash = f"{provider}:{size}:{image}"
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Find candidate
            cursor.execute(
                "SELECT * FROM resource_pool WHERE spec_hash=? AND tier='hot' AND status='available' LIMIT 1",
                (spec_hash,)
            )
            row = cursor.fetchone()
            
            if row:
                # Atomic Claim
                pool_id = row['id']
                cursor.execute(
                    "UPDATE resource_pool SET status='claimed', updated_at=? WHERE id=? AND status='available'",
                    (int(time.time()), pool_id)
                )
                
                if cursor.rowcount == 1:
                    logger.info(f"Claimed Hot Resource: {row['logical_id']}")
                    return dict(row)
        
        return None