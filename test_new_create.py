#!/usr/bin/env python3
"""
Test the new create command directly
"""

import sys
import os

# Add CloudBrew to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from LCF.intelligent_builder import IntelligentBuilder

def test_new_create():
    """Test the new create functionality"""
    
    builder = IntelligentBuilder()
    
    # Test 1: Basic instance creation
    print("Test 1: Basic AWS instance")
    config = builder.build_configuration('aws_instance', {
        'name': 'web-worker',
        'autoscale': {
            'min_size': 1,
            'max_size': 5,
            'metric': 'cpu',
            'threshold': 70.0,
            'cooldown': 60
        }
    })
    hcl = builder._config_to_hcl(config)
    print("SUCCESS!")
    print(hcl)
    print()
    
    # Test 2: With spec file
    print("Test 2: With spec file")
    spec_data = {
        'type': 'aws_autoscaling_group',
        'min_size': 1,
        'max_size': 5,
        'desired_capacity': 2
    }
    config = builder.build_configuration('aws_autoscaling_group', spec_data)
    hcl = builder._config_to_hcl(config)
    print("SUCCESS!")
    print(hcl)

if __name__ == "__main__":
    test_new_create()