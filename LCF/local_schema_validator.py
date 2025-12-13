#!/usr/bin/env python3

"""
Local Schema-Based Validation System for CloudBrew
Replaces OpenTofu validation with fast, local schema validation
"""

import json
import os
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import re

# Built-in schemas for common AWS, GCP, and Azure resources
# This eliminates the need for OpenTofu provider schema fetching

class LocalSchemaValidator:
    """
    Fast, local schema validation that replaces OpenTofu validation.
    Uses built-in schemas and intelligent defaults.
    """
    
    def __init__(self):
        self.schemas = self._load_built_in_schemas()
        self.knowledge_base = self._load_knowledge_base()
    
    def _load_built_in_schemas(self) -> Dict[str, Dict]:
        """Load comprehensive built-in schemas for AWS, GCP, and Azure"""
        return {
            # AWS Schemas
            'aws_instance': {
                'attributes': {
                    'ami': {'type': 'string', 'required': True},
                    'instance_type': {'type': 'string', 'required': True},
                    'subnet_id': {'type': 'string'},
                    'security_groups': {'type': 'list', 'element_type': 'string'},
                    'tags': {'type': 'map', 'element_type': 'string'},
                    'key_name': {'type': 'string'},
                    'iam_instance_profile': {'type': 'string'},
                    'user_data': {'type': 'string'},
                    'user_data_base64': {'type': 'string'},
                    'monitoring': {'type': 'bool'},
                    'disable_api_termination': {'type': 'bool'},
                    'ebs_optimized': {'type': 'bool'},
                    'associate_public_ip_address': {'type': 'bool'}
                },
                'blocks': {
                    'root_block_device': {
                        'attributes': {
                            'volume_size': {'type': 'number'},
                            'volume_type': {'type': 'string'},
                            'delete_on_termination': {'type': 'bool'}
                        }
                    },
                    'ebs_block_device': {
                        'attributes': {
                            'device_name': {'type': 'string'},
                            'volume_size': {'type': 'number'},
                            'volume_type': {'type': 'string'},
                            'delete_on_termination': {'type': 'bool'}
                        }
                    },
                    'network_interface': {
                        'attributes': {
                            'device_index': {'type': 'number'},
                            'network_interface_id': {'type': 'string'}
                        }
                    }
                }
            },
            
            'aws_s3_bucket': {
                'attributes': {
                    'bucket': {'type': 'string', 'required': True},
                    'acl': {'type': 'string'},
                    'force_destroy': {'type': 'bool'},
                    'versioning': {'type': 'map'},
                    'logging': {'type': 'map'},
                    'server_side_encryption_configuration': {'type': 'map'},
                    'tags': {'type': 'map', 'element_type': 'string'}
                }
            },
            
            'aws_dynamodb_table': {
                'attributes': {
                    'name': {'type': 'string', 'required': True},
                    'billing_mode': {'type': 'string'},
                    'read_capacity': {'type': 'number'},
                    'write_capacity': {'type': 'number'},
                    'hash_key': {'type': 'string', 'required': True},
                    'range_key': {'type': 'string'},
                    'tags': {'type': 'map', 'element_type': 'string'}
                },
                'blocks': {
                    'attribute': {
                        'attributes': {
                            'name': {'type': 'string'},
                            'type': {'type': 'string'}
                        }
                    },
                    'global_secondary_index': {
                        'attributes': {
                            'name': {'type': 'string'},
                            'hash_key': {'type': 'string'},
                            'range_key': {'type': 'string'},
                            'read_capacity': {'type': 'number'},
                            'write_capacity': {'type': 'number'},
                            'projection_type': {'type': 'string'}
                        }
                    }
                }
            },
            
            # GCP Schemas
            'google_compute_instance': {
                'attributes': {
                    'name': {'type': 'string', 'required': True},
                    'machine_type': {'type': 'string', 'required': True},
                    'zone': {'type': 'string', 'required': True},
                    'tags': {'type': 'list', 'element_type': 'string'},
                    'metadata': {'type': 'map', 'element_type': 'string'},
                    'metadata_startup_script': {'type': 'string'},
                    'allow_stopping_for_update': {'type': 'bool'},
                    'can_ip_forward': {'type': 'bool'},
                    'deletion_protection': {'type': 'bool'}
                },
                'blocks': {
                    'boot_disk': {
                        'attributes': {
                            'initialize_params': {
                                'type': 'map',
                                'attributes': {
                                    'image': {'type': 'string'},
                                    'size': {'type': 'number'},
                                    'type': {'type': 'string'}
                                }
                            }
                        }
                    },
                    'network_interface': {
                        'attributes': {
                            'network': {'type': 'string'},
                            'subnetwork': {'type': 'string'},
                            'access_config': {'type': 'list'}
                        }
                    },
                    'service_account': {
                        'attributes': {
                            'email': {'type': 'string'},
                            'scopes': {'type': 'list', 'element_type': 'string'}
                        }
                    }
                }
            },
            
            'google_storage_bucket': {
                'attributes': {
                    'name': {'type': 'string', 'required': True},
                    'location': {'type': 'string'},
                    'storage_class': {'type': 'string'},
                    'versioning': {'type': 'map'},
                    'logging': {'type': 'map'},
                    'lifecycle_rule': {'type': 'list'},
                    'labels': {'type': 'map', 'element_type': 'string'}
                }
            },
            
            # Azure Schemas
            'azurerm_virtual_machine': {
                'attributes': {
                    'name': {'type': 'string', 'required': True},
                    'location': {'type': 'string', 'required': True},
                    'resource_group_name': {'type': 'string', 'required': True},
                    'vm_size': {'type': 'string', 'required': True},
                    'tags': {'type': 'map', 'element_type': 'string'},
                    'delete_os_disk_on_termination': {'type': 'bool'},
                    'delete_data_disks_on_termination': {'type': 'bool'},
                    'license_type': {'type': 'string'}
                },
                'blocks': {
                    'storage_os_disk': {
                        'attributes': {
                            'name': {'type': 'string'},
                            'caching': {'type': 'string'},
                            'create_option': {'type': 'string'},
                            'managed_disk_type': {'type': 'string'},
                            'disk_size_gb': {'type': 'number'}
                        }
                    },
                    'storage_data_disk': {
                        'attributes': {
                            'name': {'type': 'string'},
                            'managed_disk_type': {'type': 'string'},
                            'create_option': {'type': 'string'},
                            'lun': {'type': 'number'},
                            'disk_size_gb': {'type': 'number'}
                        }
                    },
                    'storage_image_reference': {
                        'attributes': {
                            'publisher': {'type': 'string'},
                            'offer': {'type': 'string'},
                            'sku': {'type': 'string'},
                            'version': {'type': 'string'}
                        }
                    },
                    'os_profile': {
                        'attributes': {
                            'computer_name': {'type': 'string'},
                            'admin_username': {'type': 'string'},
                            'admin_password': {'type': 'string'},
                            'custom_data': {'type': 'string'}
                        }
                    },
                    'os_profile_linux_config': {
                        'attributes': {
                            'disable_password_authentication': {'type': 'bool'},
                            'ssh_keys': {'type': 'list'}
                        }
                    }
                }
            },
            
            'azurerm_storage_account': {
                'attributes': {
                    'name': {'type': 'string', 'required': True},
                    'resource_group_name': {'type': 'string', 'required': True},
                    'location': {'type': 'string', 'required': True},
                    'account_tier': {'type': 'string', 'required': True},
                    'account_replication_type': {'type': 'string', 'required': True},
                    'tags': {'type': 'map', 'element_type': 'string'},
                    'enable_https_traffic_only': {'type': 'bool'},
                    'min_tls_version': {'type': 'string'},
                    'allow_blob_public_access': {'type': 'bool'}
                }
            }
        }
    
    def _load_knowledge_base(self) -> Dict:
        """Load knowledge base with intelligent defaults"""
        return {
            'aws_instance': {
                'ami': 'ami-0c55b159cbfafe1f0',  # Ubuntu 20.04 LTS
                'instance_type': 't3.micro',
                'monitoring': True,
                'tags': {'Name': 'cloudbrew-instance', 'Environment': 'dev'}
            },
            'google_compute_instance': {
                'machine_type': 'e2-micro',
                'zone': 'us-central1-a',
                'tags': ['http-server', 'https-server'],
                'metadata': {'startup-script': '#!/bin/bash\necho "Hello, World!" > index.html\nnohup python -m SimpleHTTPServer 80 &'}
            },
            'azurerm_virtual_machine': {
                'vm_size': 'Standard_B1s',
                'license_type': 'None',
                'tags': {'Environment': 'Development'}
            }
        }
    
    def validate(self, resource_type: str, config: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        Validate a resource configuration using local schemas.
        Returns (is_valid, validation_result)
        """
        if resource_type not in self.schemas:
            # For unknown resource types, we'll be permissive and return valid
            # This allows the system to work with new resource types
            return True, {
                'valid': True,
                'message': f'Resource type {resource_type} not in local schema cache - assuming valid',
                'config': config,
                'warnings': [f'Unknown resource type: {resource_type}']
            }
        
        schema = self.schemas[resource_type]
        validation_result = {
            'valid': True,
            'message': 'Configuration is valid',
            'config': config,
            'warnings': [],
            'errors': []
        }
        
        # Apply intelligent defaults from knowledge base
        if resource_type in self.knowledge_base:
            for key, value in self.knowledge_base[resource_type].items():
                if key not in config:
                    config[key] = value
                    validation_result['warnings'].append(f'Applied default value for {key}: {value}')
        
        # Validate required attributes
        for attr_name, attr_schema in schema['attributes'].items():
            if attr_schema.get('required', False):
                if attr_name not in config:
                    validation_result['valid'] = False
                    validation_result['errors'].append(f'Missing required attribute: {attr_name}')
        
        # Validate attribute types
        for attr_name, attr_value in config.items():
            if attr_name in schema['attributes']:
                expected_type = schema['attributes'][attr_name]['type']
                if not self._validate_type(attr_value, expected_type, schema['attributes'][attr_name]):
                    validation_result['valid'] = False
                    validation_result['errors'].append(f'Invalid type for {attr_name}: expected {expected_type}, got {type(attr_value).__name__}')
        
        return validation_result['valid'], validation_result
    
    def _validate_type(self, value, expected_type: str, schema: Dict) -> bool:
        """Validate that a value matches the expected type"""
        if expected_type == 'string':
            return isinstance(value, str)
        elif expected_type == 'number':
            return isinstance(value, (int, float))
        elif expected_type == 'bool':
            return isinstance(value, bool)
        elif expected_type == 'list':
            if not isinstance(value, list):
                return False
            # Validate list elements if element_type is specified
            if 'element_type' in schema:
                element_type = schema['element_type']
                return all(self._validate_type(item, element_type, {}) for item in value)
            return True
        elif expected_type == 'map':
            if not isinstance(value, dict):
                return False
            # Validate map values if element_type is specified
            if 'element_type' in schema:
                element_type = schema['element_type']
                return all(self._validate_type(v, element_type, {}) for v in value.values())
            return True
        else:
            # Unknown type - be permissive
            return True
    
    def get_intelligent_defaults(self, resource_type: str) -> Dict[str, Any]:
        """Get intelligent defaults for a resource type"""
        return self.knowledge_base.get(resource_type, {})
    
    def add_schema(self, resource_type: str, schema: Dict) -> None:
        """Add a new schema to the validator"""
        self.schemas[resource_type] = schema
        # Save to persistent storage
        self._save_schemas()
    
    def _save_schemas(self) -> None:
        """Save schemas to persistent storage"""
        try:
            with open('local_schemas.json', 'w') as f:
                json.dump(self.schemas, f, indent=2)
        except Exception:
            # Silent failure - not critical
            pass
    
    def load_external_schemas(self, file_path: str) -> None:
        """Load additional schemas from an external file"""
        try:
            with open(file_path, 'r') as f:
                external_schemas = json.load(f)
            self.schemas.update(external_schemas)
        except Exception as e:
            raise ValueError(f'Failed to load external schemas: {e}')

# Global instance for easy access
local_validator = LocalSchemaValidator()

if __name__ == "__main__":
    # Test the validator
    validator = LocalSchemaValidator()
    
    # Test AWS instance validation
    test_config = {
        'ami': 'ami-12345678',
        'instance_type': 't3.micro',
        'tags': {'Name': 'test'}
    }
    
    valid, result = validator.validate('aws_instance', test_config)
    print(f"AWS Instance Validation: {'PASS' if valid else 'FAIL'}")
    print(f"Result: {result}")
    
    # Test with missing required field
    invalid_config = {'instance_type': 't3.micro'}
    valid, result = validator.validate('aws_instance', invalid_config)
    print(f"\nInvalid Config Validation: {'PASS' if not valid else 'FAIL'}")
    print(f"Errors: {result.get('errors', [])}")