#!/usr/bin/env python3

"""
Test Validation Wrapper for CloudBrew
Provides a fast validation interface for test suites
"""

import json
import sys
import os
from typing import Dict, Any, Tuple
from LCF.fast_validation import fast_validator
from LCF.fast_resource_resolver import fast_resolver

class TestValidationWrapper:
    """
    Wrapper class that provides fast validation for test suites.
    Replaces the slow OpenTofu validation with local schema validation.
    """
    
    def __init__(self):
        self.validator = fast_validator
        self.resolver = fast_resolver
    
    def validate_resource(self, resource_type: str, config: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        Validate a resource configuration using fast validation.
        This is the main interface for test suites.
        """
        return self.validator.validate_resource(resource_type, config)
    
    def get_fast_validation_result(self, resource_type: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get a comprehensive validation result for test reporting.
        """
        is_valid, result = self.validate_resource(resource_type, config)
        
        # Format result for test reporting
        test_result = {
            'resource_type': resource_type,
            'success': is_valid and result.get('success', False),
            'valid': is_valid,
            'validation_time_ms': result.get('validation_time_ms', 0),
            'warnings': result.get('warnings', []),
            'errors': result.get('errors', []),
            'hcl_generated': bool(result.get('hcl')),
            'config_used': result.get('config', {})
        }
        
        # Add HCL if validation was successful
        if test_result['success'] and 'hcl' in result:
            test_result['hcl'] = result['hcl']
        
        return test_result
    
    def batch_validate(self, resource_configs: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """
        Batch validate multiple resource configurations.
        Returns a dictionary with results for each resource type.
        """
        results = {}
        
        for resource_type, config in resource_configs.items():
            results[resource_type] = self.get_fast_validation_result(resource_type, config)
        
        return results
    
    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get performance metrics for the validation system"""
        return self.validator.get_metrics()

# Global instance for easy access
test_validator = TestValidationWrapper()

def validate_resource_for_test(resource_type: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convenience function for test suites to validate a single resource.
    Returns a comprehensive result dictionary.
    """
    return test_validator.get_fast_validation_result(resource_type, config)

if __name__ == "__main__":
    # Test the validation wrapper
    wrapper = TestValidationWrapper()
    
    # Test AWS instance
    test_config = {
        'ami': 'ami-12345678',
        'instance_type': 't3.micro',
        'tags': {'Name': 'test-instance', 'Environment': 'test'}
    }
    
    print("Testing Validation Wrapper...")
    print("=" * 50)
    
    result = wrapper.get_fast_validation_result('aws_instance', test_config)
    
    print(f"Resource Type: {result['resource_type']}")
    print(f"Success: {result['success']}")
    print(f"Valid: {result['valid']}")
    print(f"Validation Time: {result['validation_time_ms']} ms")
    print(f"Warnings: {len(result['warnings'])}")
    print(f"Errors: {len(result['errors'])}")
    print(f"HCL Generated: {result['hcl_generated']}")
    
    if result['hcl_generated']:
        print("\nGenerated HCL:")
        print(result['hcl'])
    
    # Test performance metrics
    print("\nPerformance Metrics:")
    metrics = wrapper.get_performance_metrics()
    print(f"Total Validations: {metrics['total_validations']}")
    print(f"Average Time: {metrics['average_time_ms']:.2f} ms")
    print(f"Success Rate: {metrics['success_rate'] * 100:.1f}%")