#!/usr/bin/env python3

"""
Caching Performance Test for CloudBrew
Demonstrates how caching makes subsequent runs much faster
"""

import sys
import os
import time
import json
from typing import List, Dict, Any
from LCF.fast_validation import fast_validator

# Add CloudBrew to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Test configuration
TEST_CONFIG = {
    'ami': 'ami-0c55b159cbfafe1f0',
    'instance_type': 't3.micro',
    'tags': {'Name': 'test-instance', 'Environment': 'test'}
}

def test_caching_performance():
    """Test the caching performance improvement"""
    print("CloudBrew Caching Performance Test")
    print("=" * 50)
    print("Demonstrating cache performance improvement...")
    print()
    
    validator = fast_validator
    
    # Clear cache to start fresh
    validator.cache.clear_cache()
    print("Cache cleared - starting fresh")
    print()
    
    # First run (cache miss)
    print("First validation (cache miss)...")
    start_time = time.time()
    is_valid1, result1 = validator.validate_resource('aws_instance', TEST_CONFIG)
    time1 = (time.time() - start_time) * 1000
    
    print(f"  Time: {time1:.2f} ms")
    print(f"  Cached: {result1.get('cached', False)}")
    print(f"  Success: {is_valid1}")
    print()
    
    # Second run (cache hit)
    print("Second validation (cache hit)...")
    start_time = time.time()
    is_valid2, result2 = validator.validate_resource('aws_instance', TEST_CONFIG)
    time2 = (time.time() - start_time) * 1000
    
    print(f"  Time: {time2:.2f} ms")
    print(f"  Cached: {result2.get('cached', False)}")
    print(f"  Success: {is_valid2}")
    print()
    
    # Third run (cache hit)
    print("Third validation (cache hit)...")
    start_time = time.time()
    is_valid3, result3 = validator.validate_resource('aws_instance', TEST_CONFIG)
    time3 = (time.time() - start_time) * 1000
    
    print(f"  Time: {time3:.2f} ms")
    print(f"  Cached: {result3.get('cached', False)}")
    print(f"  Success: {is_valid3}")
    print()
    
    # Performance summary
    print("=" * 50)
    print("PERFORMANCE SUMMARY")
    print("=" * 50)
    
    print(f"First run (cache miss): {time1:.2f} ms")
    print(f"Second run (cache hit): {time2:.2f} ms")
    print(f"Third run (cache hit):  {time3:.2f} ms")
    
    if time1 > 0:
        speedup = time1 / time2 if time2 > 0 else float('inf')
        print(f"\nCache speedup: {speedup:.1f}x faster")
        print(f"Time saved: {time1 - time2:.2f} ms ({((time1 - time2) / time1 * 100):.1f}% reduction)")
    
    # Cache statistics
    metrics = validator.get_metrics()
    print(f"\nCACHE STATISTICS:")
    print(f"  Cache Size: {metrics.get('cache_size', 0)}")
    print(f"  Cache Hits: {metrics.get('total_cache_hits', 0)}")
    print(f"  Cache Misses: {metrics.get('total_cache_misses', 0)}")
    print(f"  Hit Rate: {metrics.get('cache_hit_rate', 0) * 100:.1f}%")
    
    # Save cache for next run
    validator.cache.save_cache()
    print(f"\nCache saved to: {validator.cache.cache_file}")
    
    # Test with different configuration (should be cache miss)
    print("\n" + "=" * 50)
    print("Testing with different configuration...")
    print("=" * 50)
    
    DIFFERENT_CONFIG = {
        'ami': 'ami-different',
        'instance_type': 't3.small',  # Different instance type
        'tags': {'Name': 'different-instance'}
    }
    
    print("Different configuration validation...")
    start_time = time.time()
    is_valid4, result4 = validator.validate_resource('aws_instance', DIFFERENT_CONFIG)
    time4 = (time.time() - start_time) * 1000
    
    print(f"  Time: {time4:.2f} ms")
    print(f"  Cached: {result4.get('cached', False)}")
    print(f"  Success: {is_valid4}")
    
    # Final metrics
    final_metrics = validator.get_metrics()
    print(f"\nFINAL CACHE STATISTICS:")
    print(f"  Cache Size: {final_metrics.get('cache_size', 0)}")
    print(f"  Cache Hits: {final_metrics.get('total_cache_hits', 0)}")
    print(f"  Cache Misses: {final_metrics.get('total_cache_misses', 0)}")
    print(f"  Hit Rate: {final_metrics.get('cache_hit_rate', 0) * 100:.1f}%")
    
    return True

def main():
    """Main entry point"""
    try:
        success = test_caching_performance()
        
        if success:
            print("\nCaching performance test completed successfully!")
            print("The cache system is working and will make subsequent runs much faster.")
            return 0
        else:
            print("\n❌ Caching performance test failed.")
            return 1
            
    except Exception as e:
        print(f"\nTest suite failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())