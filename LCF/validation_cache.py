#!/usr/bin/env python3

"""
Validation Cache System for CloudBrew
Persistent caching to make subsequent validations even faster
"""

import json
import os
import time
import hashlib
from typing import Dict, Any, Optional, Tuple
from pathlib import Path

class ValidationCache:
    """
    Persistent cache system for validation results.
    Stores validated configurations and their HCL output for instant reuse.
    """
    
    def __init__(self, cache_dir: str = ".cloudbrew_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self.cache_file = self.cache_dir / "validation_cache.json"
        self.cache = self._load_cache()
        self.hits = 0
        self.misses = 0
    
    def _load_cache(self) -> Dict[str, Any]:
        """Load cache from disk"""
        try:
            if self.cache_file.exists():
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return {}
    
    def _save_cache(self) -> None:
        """Save cache to disk"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, indent=2)
        except Exception:
            # Silent failure - cache is not critical
            pass
    
    def _get_cache_key(self, resource_type: str, config: Dict[str, Any]) -> str:
        """Generate a unique cache key for a resource configuration"""
        # Create a hash of the configuration (excluding volatile fields)
        config_copy = config.copy()
        
        # Remove fields that shouldn't affect caching
        for field in ['name', 'tags', 'labels', 'metadata']:
            config_copy.pop(field, None)
        
        # Create a stable string representation
        config_str = json.dumps(config_copy, sort_keys=True)
        cache_key = f"{resource_type}:{hashlib.md5(config_str.encode()).hexdigest()}"
        
        return cache_key
    
    def get_cached_result(self, resource_type: str, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get cached validation result if available"""
        cache_key = self._get_cache_key(resource_type, config)
        
        if cache_key in self.cache:
            # Update access time
            self.cache[cache_key]['last_accessed'] = time.time()
            self.hits += 1
            return self.cache[cache_key]
        
        self.misses += 1
        return None
    
    def cache_validation_result(self, resource_type: str, config: Dict[str, Any], result: Dict[str, Any]) -> None:
        """Cache a validation result for future use"""
        cache_key = self._get_cache_key(resource_type, config)
        
        # Store relevant parts of the result
        cached_result = {
            'resource_type': resource_type,
            'config': config,
            'hcl': result.get('hcl'),
            'warnings': result.get('warnings', []),
            'valid': result.get('valid', False),
            'success': result.get('success', False),
            'cached_at': time.time(),
            'last_accessed': time.time()
        }
        
        self.cache[cache_key] = cached_result
        
        # Save to disk periodically (every 10 cache operations)
        if len(self.cache) % 10 == 0:
            self._save_cache()
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        return {
            'cache_size': len(self.cache),
            'hit_rate': self.hits / (self.hits + self.misses) if (self.hits + self.misses) > 0 else 0,
            'hits': self.hits,
            'misses': self.misses,
            'cache_file': str(self.cache_file)
        }
    
    def clear_cache(self) -> None:
        """Clear the cache"""
        self.cache = {}
        self.hits = 0
        self.misses = 0
        try:
            if self.cache_file.exists():
                self.cache_file.unlink()
        except Exception:
            pass
    
    def save_cache(self) -> None:
        """Force save cache to disk"""
        self._save_cache()

# Global cache instance
validation_cache = ValidationCache()

if __name__ == "__main__":
    # Test the validation cache
    cache = ValidationCache()
    
    # Test caching
    test_config = {
        'ami': 'ami-12345678',
        'instance_type': 't3.micro',
        'monitoring': True
    }
    
    test_result = {
        'valid': True,
        'success': True,
        'hcl': 'resource "aws_instance" "test" { ami = "ami-12345678" }',
        'warnings': ['Applied default for monitoring']
    }
    
    print("Testing Validation Cache...")
    print("=" * 40)
    
    # First access (miss)
    cached = cache.get_cached_result('aws_instance', test_config)
    print(f"First access: {'HIT' if cached else 'MISS'} (expected: MISS)")
    
    # Cache the result
    cache.cache_validation_result('aws_instance', test_config, test_result)
    print("Cached the validation result")
    
    # Second access (hit)
    cached = cache.get_cached_result('aws_instance', test_config)
    print(f"Second access: {'HIT' if cached else 'MISS'} (expected: HIT)")
    
    if cached:
        print(f"Cached HCL: {cached.get('hcl', 'None')}")
    
    # Test cache stats
    stats = cache.get_cache_stats()
    print(f"\nCache Stats:")
    print(f"  Size: {stats['cache_size']}")
    print(f"  Hits: {stats['hits']}")
    print(f"  Misses: {stats['misses']}")
    print(f"  Hit Rate: {stats['hit_rate'] * 100:.1f}%")
    
    # Save cache
    cache.save_cache()
    print(f"\nCache saved to: {stats['cache_file']}")