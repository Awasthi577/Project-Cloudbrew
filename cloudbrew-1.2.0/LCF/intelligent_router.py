"""
LCF/intelligent_router.py
Routing engine for provisioning requests.
Decides between Hot Cache (L1), Warm Cache (L2), or Cold Build (L3).
Includes Spot Arbitrage logic.
"""

import logging
from typing import Dict, Any, Tuple
import time

from LCF.pool_manager import WarmPoolManager
from LCF.cloud_adapters.opentofu_adapter import OpenTofuAdapter

logger = logging.getLogger(__name__)

class IntelligentRouter:
    """
    Routes provisioning requests to the optimal fulfillment method.
    """

    def __init__(self):
        self.pool_manager = WarmPoolManager()
        self.adapter = OpenTofuAdapter()

    def provision(self, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main entrypoint.
        """
        start_ts = time.time()
        
        # 1. Normalize Spec
        # Default to opentofu if not specified
        provider = spec.get("provider", "opentofu")
        size = spec.get("size", "small")
        image = spec.get("image", "ubuntu-22.04")

        # 2. Check L1 Cache (Hot Pool)
        # Note: We only check if provider is explicit or 'auto' resolves to supported pool types
        if provider in ["aws", "auto"]:
            # Hardcoded preference for AWS in 'auto' mode for this demo
            target_provider = "aws" if provider == "auto" else provider
            
            logger.info(f"Checking L1 Hot Pool for {target_provider}:{size}...")
            cached_vm = self.pool_manager.get_available_resource(target_provider, size, image)
            
            if cached_vm:
                return {
                    "source": "L1_CACHE_HIT",
                    "id": cached_vm['logical_id'],
                    "provider": target_provider,
                    "details": cached_vm['details'],
                    "latency": f"{time.time() - start_ts:.4f}s",
                    "msg": "Instant provision from Hot Pool"
                }

        # 3. L2 Cache (Warm Pool)
        # (Logic similar to L1, but would involve sending a 'start' command to the cloud)
        # cached_warm = ... 
        
        # 4. Arbitrage (Smart Cold Start)
        if provider == "auto":
            best_provider, reason = self._arbitrage_decision(size, spec.get("region", "us-east-1"))
            spec["provider"] = best_provider
            logger.info(f"Arbitrage Engine selected {best_provider}: {reason}")
        
        # 5. L3 Cold Build (Standard)
        logger.info("Cache Miss. Initiating standard cold build via OpenTofu...")
        result = self.adapter.create_instance(name, spec, plan_only=False)
        
        result["source"] = "L3_COLD_BUILD"
        result["latency"] = f"{time.time() - start_ts:.2f}s"
        return result

    def _arbitrage_decision(self, size: str, region: str) -> Tuple[str, str]:
        """
        Mocked logic to pick cheapest provider.
        In prod, this queries an API like Vantage or Infracost.
        """
        # Mock Pricing Table
        prices = {
            "aws":   {"small": 0.023, "medium": 0.046}, # t3.micro
            "azure": {"small": 0.020, "medium": 0.080}, # B1s
            "gcp":   {"small": 0.025, "medium": 0.050}  # e2-micro
        }
        
        size_key = "small" if "small" in size else "medium"
        
        # Find min price
        candidates = []
        for p, price_map in prices.items():
            if size_key in price_map:
                candidates.append((p, price_map[size_key]))
        
        if not candidates:
            return "aws", "Default fallback"

        best = min(candidates, key=lambda x: x[1])
        return best[0], f"Lowest price ${best[1]}/hr"