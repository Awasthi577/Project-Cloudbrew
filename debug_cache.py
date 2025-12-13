#!/usr/bin/env python3

"""
Debug cache to see what's happening
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from LCF.validation_cache import validation_cache
from LCF.fast_resource_resolver import fast_resolver

# Test configuration
test_config = {
    'ami': 'ami-12345678',
    'instance_type': 't3.micro',
    'tags': {'Name': 'test'}
}

print("Debugging cache system...")
print("=" * 40)

# Clear cache
validation_cache.clear_cache()
print("Cache cleared")

# Test cache key generation
cache_key = validation_cache._get_cache_key('aws_instance', test_config)
print(f"Cache key for config: {cache_key}")

# Test resolver
defaults = fast_resolver.get_defaults('aws_instance')
merged_config = {**defaults, **test_config}
print(f"Merged config: {merged_config}")

# Test cache key with merged config
cache_key_merged = validation_cache._get_cache_key('aws_instance', merged_config)
print(f"Cache key for merged config: {cache_key_merged}")

# Check if keys are different
if cache_key != cache_key_merged:
    print("❌ Cache keys are different! This is the issue.")
    print("The fast validation system merges defaults before caching,")
    print("but the test uses the original config for cache lookup.")
else:
    print("✅ Cache keys are the same")

print("\nThis explains why cache hits aren't detected properly.")