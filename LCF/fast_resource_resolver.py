#!/usr/bin/env python3

"""
Fast Resource Resolver for CloudBrew
Optimized version that uses local schemas instead of OpenTofu
"""

import json
import os
import time
from typing import Dict, Any, Optional, Tuple
from LCF.local_schema_validator import local_validator

class FastResourceResolver:
    """
    Fast resource resolver that uses local schemas and knowledge base.
    Replaces the slow OpenTofu-based resolver.
    """
    
    def __init__(self):
        self.validator = local_validator
        self.knowledge_base = self._load_knowledge_base()
        self.alias_mapping = self._load_alias_mapping()
    
    def _load_knowledge_base(self) -> Dict:
        """Load knowledge base with resource information"""
        return {
            # AWS resources
            'aws_instance': {
                '_resolved': 'aws_instance',
                '_provider': 'aws',
                '_defaults': {
                    'ami': 'ami-0c55b159cbfafe1f0',  # Ubuntu 20.04 LTS
                    'instance_type': 't3.micro',
                    'monitoring': True,
                    'tags': {'Name': 'cloudbrew-instance', 'Environment': 'dev'}
                }
            },
            'aws_s3_bucket': {
                '_resolved': 'aws_s3_bucket',
                '_provider': 'aws',
                '_defaults': {
                    'acl': 'private',
                    'force_destroy': False
                }
            },
            'aws_dynamodb_table': {
                '_resolved': 'aws_dynamodb_table',
                '_provider': 'aws',
                '_defaults': {
                    'billing_mode': 'PAY_PER_REQUEST',
                    'hash_key': 'id'
                }
            },
            
            # GCP resources
            'google_compute_instance': {
                '_resolved': 'google_compute_instance',
                '_provider': 'google',
                '_defaults': {
                    'machine_type': 'e2-micro',
                    'zone': 'us-central1-a',
                    'tags': ['http-server', 'https-server']
                }
            },
            'google_storage_bucket': {
                '_resolved': 'google_storage_bucket',
                '_provider': 'google',
                '_defaults': {
                    'location': 'US',
                    'storage_class': 'STANDARD'
                }
            },
            
            # Azure resources
            'azurerm_virtual_machine': {
                '_resolved': 'azurerm_virtual_machine',
                '_provider': 'azurerm',
                '_defaults': {
                    'vm_size': 'Standard_B1s',
                    'license_type': 'None'
                }
            },
            'azurerm_storage_account': {
                '_resolved': 'azurerm_storage_account',
                '_provider': 'azurerm',
                '_defaults': {
                    'account_tier': 'Standard',
                    'account_replication_type': 'LRS'
                }
            },
            
            # Common aliases
            'vm': {
                '_resolved': 'aws_instance',  # Default to AWS
                '_provider': 'aws',
                '_defaults': {
                    'ami': 'ami-0c55b159cbfafe1f0',
                    'instance_type': 't3.micro'
                }
            },
            'instance': {
                '_resolved': 'aws_instance',
                '_provider': 'aws',
                '_defaults': {
                    'ami': 'ami-0c55b159cbfafe1f0',
                    'instance_type': 't3.micro'
                }
            },
            'bucket': {
                '_resolved': 'aws_s3_bucket',
                '_provider': 'aws',
                '_defaults': {
                    'acl': 'private'
                }
            }
        }
    
    def _load_alias_mapping(self) -> Dict[str, str]:
        """Load resource type aliases"""
        return {
            'vm': 'aws_instance',
            'instance': 'aws_instance',
            'ec2': 'aws_instance',
            's3': 'aws_s3_bucket',
            'bucket': 'aws_s3_bucket',
            'dynamodb': 'aws_dynamodb_table',
            'gce': 'google_compute_instance',
            'gcs': 'google_storage_bucket',
            'azure_vm': 'azurerm_virtual_machine',
            'azure_storage': 'azurerm_storage_account'
        }
    
    def resolve(self, resource_type: str, provider: str = "auto") -> Dict[str, Any]:
        """
        Fast resource resolution using local knowledge base.
        Returns resolution dictionary with _resolved, _provider, and _defaults.
        """
        # Handle aliases first
        resolved_type = self.alias_mapping.get(resource_type, resource_type)
        
        # Check if we have this resource in our knowledge base
        if resolved_type in self.knowledge_base:
            resolution = self.knowledge_base[resolved_type].copy()
            
            # Handle provider override
            if provider != "auto":
                resolution['_provider'] = provider
            
            return resolution
        
        # For unknown resources, provide a reasonable default
        if provider == "auto":
            # Guess provider based on resource name prefix
            if resolved_type.startswith('aws_'):
                provider = 'aws'
            elif resolved_type.startswith('google_'):
                provider = 'google'
            elif resolved_type.startswith('azurerm_'):
                provider = 'azurerm'
            else:
                provider = 'aws'  # Default to AWS
        
        return {
            '_resolved': resolved_type,
            '_provider': provider,
            '_defaults': {}
        }
    
    def get_defaults(self, resource_type: str, provider: str = "auto") -> Dict[str, Any]:
        """Get intelligent defaults for a resource type"""
        resolution = self.resolve(resource_type, provider)
        return resolution.get('_defaults', {})
    
    def validate(self, resource_type: str, config: Dict[str, Any], provider: str = "auto") -> Tuple[bool, Dict[str, Any]]:
        """Validate a resource configuration using fast validation"""
        resolution = self.resolve(resource_type, provider)
        resolved_type = resolution['_resolved']
        
        # Apply defaults
        defaults = resolution.get('_defaults', {})
        merged_config = {**defaults, **config}
        
        # Use local schema validator
        is_valid, validation_result = self.validator.validate(resolved_type, merged_config)
        
        # Add resolution info to validation result
        validation_result['_resolution'] = resolution
        
        return is_valid, validation_result

# Global instance for easy access
fast_resolver = FastResourceResolver()

if __name__ == "__main__":
    # Test the fast resolver
    resolver = FastResourceResolver()
    
    print("Testing Fast Resource Resolver...")
    print("=" * 50)
    
    # Test resolution
    test_cases = [
        ('vm', 'auto'),
        ('aws_instance', 'aws'),
        ('google_compute_instance', 'google'),
        ('unknown_resource', 'auto')
    ]
    
    for resource_type, provider in test_cases:
        start_time = time.time()
        result = resolver.resolve(resource_type, provider)
        end_time = time.time()
        
        print(f"Resolve {resource_type} (provider={provider}):")
        print(f"  Time: {(end_time - start_time) * 1000:.2f} ms")
        print(f"  Resolved: {result.get('_resolved')}")
        print(f"  Provider: {result.get('_provider')}")
        print(f"  Defaults: {len(result.get('_defaults', {}))} defaults")
        print()
    
    # Test validation
    print("Testing validation...")
    test_config = {'ami': 'ami-12345', 'instance_type': 't3.micro'}
    start_time = time.time()
    is_valid, result = resolver.validate('aws_instance', test_config)
    end_time = time.time()
    
    print(f"Validation Time: {(end_time - start_time) * 1000:.2f} ms")
    print(f"Valid: {is_valid}")
    print(f"Warnings: {result.get('warnings', [])}")
    print(f"Errors: {result.get('errors', [])}")