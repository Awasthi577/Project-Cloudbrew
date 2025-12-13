#!/usr/bin/env python3

"""
Fast Validation System for CloudBrew
Replaces OpenTofu validation with local schema-based validation
"""

import json
import time
from typing import Dict, Any, Tuple
from LCF.local_schema_validator import local_validator
from LCF.resource_resolver import ResourceResolver
from LCF.validation_cache import validation_cache

class FastValidationSystem:
    """
    Fast validation system that uses local schemas instead of OpenTofu.
    Provides instant validation and intelligent defaults.
    """
    
    def __init__(self):
        self.validator = local_validator
        self.resolver = ResourceResolver()
        self.cache = validation_cache
        self.performance_metrics = {
            'total_validations': 0,
            'average_time_ms': 0,
            'success_rate': 0.0,
            'cache_hits': 0,
            'cache_misses': 0
        }
    
    def validate_resource(self, resource_type: str, config: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        Validate a resource configuration using fast local validation.
        Returns (is_valid, validation_result)
        """
        start_time = time.time()
        
        try:
            # Step 0: Apply intelligent defaults first (for consistent cache keys)
            defaults = self.validator.get_intelligent_defaults(resource_type)
            merged_config = {**defaults, **config}
            
            # Step 1: Check cache with merged config (instant return if cached)
            cached_result = self.cache.get_cached_result(resource_type, merged_config)
            if cached_result:
                # Cache hit - return immediately
                validation_time = time.time() - start_time
                self._update_metrics(True, validation_time, cache_hit=True)
                
                # Create a proper result dictionary with cache info
                result = cached_result.copy()
                result['validation_time_ms'] = validation_time * 1000
                result['cached'] = True
                result['cache_hit'] = True
                
                return cached_result['valid'], result
            
            # Step 2: Resolve resource type (handles aliases)
            resolved = self.resolver.resolve(resource_type, provider="auto")
            if isinstance(resolved, dict) and '_resolved' in resolved:
                resource_type = resolved['_resolved']
            
            # Step 3: Fast schema validation
            is_valid, validation_result = self.validator.validate(resource_type, merged_config)
            
            # Step 4: Generate HCL configuration
            if is_valid:
                validation_result['hcl'] = self._generate_hcl(resource_type, merged_config)
                validation_result['success'] = True
            else:
                validation_result['success'] = False
            
            # Step 5: Cache the result for future use
            self.cache.cache_validation_result(resource_type, merged_config, validation_result)
            
            # Step 6: Update performance metrics
            self._update_metrics(is_valid, time.time() - start_time, cache_hit=False)
            
            return is_valid, validation_result
            
        except Exception as e:
            end_time = time.time()
            self._update_metrics(False, end_time - start_time)
            
            return False, {
                'valid': False,
                'success': False,
                'message': f'Validation error: {str(e)}',
                'config': config,
                'errors': [f'Validation exception: {str(e)}'],
                'warnings': [],
                'validation_time_ms': int((end_time - start_time) * 1000)
            }
    
    def _update_metrics(self, success: bool, duration: float, cache_hit: bool = False) -> None:
        """Update performance metrics"""
        self.performance_metrics['total_validations'] += 1
        time_ms = duration * 1000
        
        if cache_hit:
            self.performance_metrics['cache_hits'] += 1
        else:
            self.performance_metrics['cache_misses'] += 1
        
        # Calculate moving average
        if self.performance_metrics['total_validations'] == 1:
            self.performance_metrics['average_time_ms'] = time_ms
        else:
            self.performance_metrics['average_time_ms'] = (
                (self.performance_metrics['average_time_ms'] * (self.performance_metrics['total_validations'] - 1)) + time_ms
            ) / self.performance_metrics['total_validations']
        
        # Update success rate
        if success:
            current_successes = self.performance_metrics.get('success_count', 0) + 1
            self.performance_metrics['success_count'] = current_successes
            self.performance_metrics['success_rate'] = current_successes / self.performance_metrics['total_validations']
    
    def _generate_hcl(self, resource_type: str, config: Dict[str, Any]) -> str:
        """Generate HCL configuration from validated config"""
        hcl_lines = []
        
        # Determine resource type and provider from the resource name
        if '_' in resource_type:
            provider, resource = resource_type.split('_', 1)
        else:
            provider = 'aws'  # default
            resource = resource_type
        
        # Start resource block
        hcl_lines.append(f'resource "{resource_type}" "{{name}}" {{')
        
        # Add attributes
        for key, value in config.items():
            if key.startswith('_'):  # Skip internal fields
                continue
                
            if isinstance(value, str):
                hcl_lines.append(f'  {key} = "{value}"')
            elif isinstance(value, bool):
                hcl_lines.append(f'  {key} = {str(value).lower()}')
            elif isinstance(value, (int, float)):
                hcl_lines.append(f'  {key} = {value}')
            elif isinstance(value, dict):
                hcl_lines.append(f'  {key} = {json.dumps(value)}')
            elif isinstance(value, list):
                hcl_lines.append(f'  {key} = {json.dumps(value)}')
        
        hcl_lines.append('}')
        
        return '\n'.join(hcl_lines)
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get performance metrics"""
        metrics = self.performance_metrics.copy()
        
        # Add cache stats from the cache system
        cache_stats = self.cache.get_cache_stats()
        metrics.update({
            'cache_size': cache_stats['cache_size'],
            'cache_hit_rate': cache_stats['hit_rate'],
            'total_cache_hits': cache_stats['hits'],
            'total_cache_misses': cache_stats['misses']
        })
        
        return metrics
    
    def reset_metrics(self) -> None:
        """Reset performance metrics"""
        self.performance_metrics = {
            'total_validations': 0,
            'average_time_ms': 0,
            'success_rate': 0.0
        }

# Global instance for easy access
fast_validator = FastValidationSystem()

if __name__ == "__main__":
    # Test the fast validation system
    validator = FastValidationSystem()
    
    # Test AWS instance validation
    test_config = {
        'ami': 'ami-12345678',
        'instance_type': 't3.micro',
        'tags': {'Name': 'test-instance'}
    }
    
    print("Testing Fast Validation System...")
    print("=" * 50)
    
    start_time = time.time()
    is_valid, result = validator.validate_resource('aws_instance', test_config)
    end_time = time.time()
    
    print(f"Validation Time: {(end_time - start_time) * 1000:.2f} ms")
    print(f"Result: {'VALID' if is_valid else 'INVALID'}")
    print(f"Success: {result.get('success', False)}")
    print(f"Warnings: {result.get('warnings', [])}")
    print(f"Errors: {result.get('errors', [])}")
    
    if is_valid and result.get('hcl'):
        print("\nGenerated HCL:")
        print(result['hcl'])
    
    print("\nPerformance Metrics:")
    metrics = validator.get_metrics()
    print(f"Total Validations: {metrics['total_validations']}")
    print(f"Average Time: {metrics['average_time_ms']:.2f} ms")
    print(f"Success Rate: {metrics['success_rate'] * 100:.1f}%")