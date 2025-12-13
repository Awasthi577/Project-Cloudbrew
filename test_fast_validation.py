#!/usr/bin/env python3

"""
Fast Validation Test Suite for CloudBrew
Demonstrates the performance improvement of local schema validation vs OpenTofu
"""

import sys
import os
import time
import json
from typing import List, Dict, Any
from LCF.test_validation_wrapper import validate_resource_for_test

# Add CloudBrew to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Test resources from all three cloud providers
TEST_RESOURCES = {
    # AWS Resources
    'aws_instance': {
        'ami': 'ami-0c55b159cbfafe1f0',
        'instance_type': 't3.micro',
        'tags': {'Name': 'test-instance', 'Environment': 'test'}
    },
    'aws_s3_bucket': {
        'bucket': 'test-bucket-12345',
        'acl': 'private',
        'tags': {'Name': 'test-bucket'}
    },
    'aws_dynamodb_table': {
        'name': 'test-table',
        'billing_mode': 'PAY_PER_REQUEST',
        'hash_key': 'id',
        'tags': {'Name': 'test-table'}
    },
    
    # GCP Resources
    'google_compute_instance': {
        'name': 'test-instance',
        'machine_type': 'e2-micro',
        'zone': 'us-central1-a',
        'tags': ['http-server', 'https-server']
    },
    'google_storage_bucket': {
        'name': 'test-bucket-12345',
        'location': 'US',
        'storage_class': 'STANDARD',
        'labels': {'name': 'test-bucket'}
    },
    
    # Azure Resources
    'azurerm_virtual_machine': {
        'name': 'test-vm',
        'location': 'eastus',
        'resource_group_name': 'test-rg',
        'vm_size': 'Standard_B1s',
        'tags': {'Environment': 'Test'}
    },
    'azurerm_storage_account': {
        'name': 'teststorage123',
        'resource_group_name': 'test-rg',
        'location': 'eastus',
        'account_tier': 'Standard',
        'account_replication_type': 'LRS'
    }
}

def run_fast_validation_test():
    """Run the fast validation test suite"""
    print("CloudBrew Fast Validation Test Suite")
    print("=" * 60)
    print("Testing local schema validation performance...")
    print()
    
    results = []
    total_time = 0
    success_count = 0
    
    for resource_type, config in TEST_RESOURCES.items():
        print(f"Testing {resource_type}...")
        
        start_time = time.time()
        result = validate_resource_for_test(resource_type, config)
        end_time = time.time()
        
        validation_time = (end_time - start_time) * 1000  # Convert to ms
        total_time += validation_time
        
        if result['success']:
            success_count += 1
            status = "PASS"
        else:
            status = "FAIL"
        
        print(f"  {status} - {validation_time:.2f} ms")
        print(f"  Warnings: {len(result['warnings'])}")
        print(f"  Errors: {len(result['errors'])}")
        
        results.append({
            'resource_type': resource_type,
            'success': result['success'],
            'time_ms': validation_time,
            'warnings': result['warnings'],
            'errors': result['errors']
        })
        print()
    
    # Print summary
    print("=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    print(f"Total Resources Tested: {len(TEST_RESOURCES)}")
    print(f"Successful Validations: {success_count}")
    print(f"Failed Validations: {len(TEST_RESOURCES) - success_count}")
    print(f"Success Rate: {(success_count / len(TEST_RESOURCES) * 100):.1f}%")
    print(f"Total Validation Time: {total_time:.2f} ms")
    print(f"Average Time per Resource: {total_time / len(TEST_RESOURCES):.2f} ms")
    
    # Compare with expected OpenTofu performance
    print("\n" + "=" * 60)
    print("PERFORMANCE COMPARISON")
    print("=" * 60)
    
    # Based on the executive summary:
    # Before: 30+ seconds per resource, 10.3% success rate
    # After: <1 second per resource, 96%+ success rate
    
    expected_opentofu_time = len(TEST_RESOURCES) * 30000  # 30 seconds per resource
    expected_opentofu_success = int(len(TEST_RESOURCES) * 0.103)  # 10.3% success rate
    
    our_time = total_time
    our_success = success_count
    
    speed_improvement = expected_opentofu_time / our_time if our_time > 0 else float('inf')
    success_improvement = (our_success / expected_opentofu_success) if expected_opentofu_success > 0 else float('inf')
    
    print(f"Expected OpenTofu Performance:")
    print(f"  Total Time: {expected_opentofu_time:.0f} ms ({expected_opentofu_time/1000:.0f} seconds)")
    print(f"  Expected Successes: {expected_opentofu_success}")
    print(f"  Expected Success Rate: 10.3%")
    
    print(f"\nActual Fast Validation Performance:")
    print(f"  Total Time: {our_time:.2f} ms")
    print(f"  Actual Successes: {our_success}")
    print(f"  Actual Success Rate: {(our_success / len(TEST_RESOURCES) * 100):.1f}%")
    
    print(f"\nPerformance Improvements:")
    print(f"  Speed Improvement: {speed_improvement:.1f}x faster")
    print(f"  Success Rate Improvement: {success_improvement:.1f}x better")
    
    # Save detailed results to JSON
    with open('fast_validation_results.json', 'w') as f:
        json.dump({
            'summary': {
                'total_resources': len(TEST_RESOURCES),
                'success_count': success_count,
                'failure_count': len(TEST_RESOURCES) - success_count,
                'success_rate': success_count / len(TEST_RESOURCES),
                'total_time_ms': total_time,
                'average_time_ms': total_time / len(TEST_RESOURCES)
            },
            'performance_comparison': {
                'expected_opentofu_time_ms': expected_opentofu_time,
                'expected_opentofu_success_count': expected_opentofu_success,
                'actual_time_ms': our_time,
                'actual_success_count': our_success,
                'speed_improvement': speed_improvement,
                'success_rate_improvement': success_improvement
            },
            'detailed_results': results
        }, f, indent=2)
    
    print(f"\nDetailed results saved to: fast_validation_results.json")
    
    # Return success status
    return success_count == len(TEST_RESOURCES)

def main():
    """Main entry point"""
    try:
        success = run_fast_validation_test()
        
        if success:
            print("\nAll tests passed! Fast validation is working perfectly.")
            return 0
        else:
            print("\nSome tests failed. Check the detailed results above.")
            return 1
            
    except Exception as e:
        print(f"\nTest suite failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())