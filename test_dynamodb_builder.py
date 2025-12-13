#!/usr/bin/env python3
"""
Test the Intelligent Builder with DynamoDB specifically
"""

import sys
import os

# Add the Cloudbrew directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from LCF.intelligent_builder import IntelligentBuilder

def test_dynamodb_with_debug():
    """Test DynamoDB creation with debug output"""
    
    builder = IntelligentBuilder()
    
    print("Testing Intelligent Builder with DynamoDB")
    print("=" * 50)
    
    # Test with minimal input
    user_input = {
        'name': 'users-table'
    }
    
    print(f"\nInput: {user_input}")
    print("Building configuration...")
    
    try:
        config = builder.build_configuration("aws_dynamodb_table", user_input)
        
        print("\n✅ SUCCESS! Generated configuration:")
        hcl = builder._config_to_hcl(config)
        print(hcl)
        
    except Exception as e:
        print(f"\n❌ FAILED: {e}")
        
        # Let's see what the builder is generating step by step
        print("\nDebugging step-by-step...")
        
        # Start with minimal
        config = {
            "resource": {
                "aws_dynamodb_table": {
                    "test": {}
                }
            }
        }
        
        print("\nStep 1 - Minimal config:")
        hcl = builder._config_to_hcl(config)
        print(hcl)
        
        # Add what we know is needed
        config["resource"]["aws_dynamodb_table"]["test"]["name"] = "users-table"
        config["resource"]["aws_dynamodb_table"]["test"]["billing_mode"] = "PROVISIONED"
        config["resource"]["aws_dynamodb_table"]["test"]["hash_key"] = "id"
        config["resource"]["aws_dynamodb_table"]["test"]["read_capacity"] = 5
        config["resource"]["aws_dynamodb_table"]["test"]["write_capacity"] = 5
        config["resource"]["aws_dynamodb_table"]["test"]["attribute"] = [
            {"name": "id", "type": "S"}
        ]
        
        print("\nStep 2 - With all required fields:")
        hcl = builder._config_to_hcl(config)
        print(hcl)

if __name__ == "__main__":
    test_dynamodb_with_debug()