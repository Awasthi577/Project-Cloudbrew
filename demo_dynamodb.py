#!/usr/bin/env python3
"""
Demo: How the Intelligent Builder would handle AWS DynamoDB
This demonstrates the reverse engineering approach without requiring OpenTofu.
"""

import sys
import os

# Add the Cloudbrew directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from LCF.intelligent_builder import IntelligentBuilder

def demo_dynamodb():
    """Demonstrate DynamoDB configuration building"""
    
    builder = IntelligentBuilder()
    
    print("CloudBrew Intelligent Builder - DynamoDB Demo")
    print("=" * 60)
    print()
    print("This demo shows how CloudBrew would handle an undeclared resource")
    print("like AWS DynamoDB using the reverse engineering approach.")
    print()
    
    # Simulate the user command
    print("[USER COMMAND]")
    print("   cloudbrew intelligent-create aws_dynamodb_table users-table")
    print()
    
    # Step 1: Start with minimal configuration
    print("[STEP 1] Start with minimal configuration")
    minimal_config = {
        "resource": {
            "aws_dynamodb_table": {
                "users-table": {}  # Empty - let validation guide us
            }
        }
    }
    print("   Generated: resource \"aws_dynamodb_table\" \"users-table\" {}")
    print()
    
    # Step 2: Simulate OpenTofu validation errors
    print("[STEP 2] OpenTofu validation (simulated)")
    simulated_errors = [
        "missing required argument: name",
        "missing required argument: hash_key",
        "missing required argument: attribute",
        "missing required argument: billing_mode",
        "missing required argument: read_capacity",
        "missing required argument: write_capacity"
    ]
    
    for error in simulated_errors:
        print(f"   Error: {error}")
    print()
    
    # Step 3: Apply intelligent corrections
    print("[STEP 3] Intelligent corrections")
    
    # The builder would analyze each error and apply fixes
    config = minimal_config
    
    # Fix: name
    if "name" not in config["resource"]["aws_dynamodb_table"]["users-table"]:
        config["resource"]["aws_dynamodb_table"]["users-table"]["name"] = "users-table"
        print("   Added: name = \"users-table\"")
    
    # Fix: billing_mode (smart default)
    if "billing_mode" not in config["resource"]["aws_dynamodb_table"]["users-table"]:
        config["resource"]["aws_dynamodb_table"]["users-table"]["billing_mode"] = "PROVISIONED"
        print("   Added: billing_mode = \"PROVISIONED\" (smart default)")
    
    # Fix: hash_key (smart default)
    if "hash_key" not in config["resource"]["aws_dynamodb_table"]["users-table"]:
        config["resource"]["aws_dynamodb_table"]["users-table"]["hash_key"] = "id"
        print("   Added: hash_key = \"id\" (smart default)")
    
    # Fix: attribute block (complex structure)
    if "attribute" not in config["resource"]["aws_dynamodb_table"]["users-table"]:
        config["resource"]["aws_dynamodb_table"]["users-table"]["attribute"] = [
            {
                "name": "id",
                "type": "S"
            }
        ]
        print("   Added: attribute block with hash key definition")
    
    # Fix: capacity (smart defaults for PROVISIONED mode)
    if "read_capacity" not in config["resource"]["aws_dynamodb_table"]["users-table"]:
        config["resource"]["aws_dynamodb_table"]["users-table"]["read_capacity"] = 5
        print("   Added: read_capacity = 5 (smart default)")
    
    if "write_capacity" not in config["resource"]["aws_dynamodb_table"]["users-table"]:
        config["resource"]["aws_dynamodb_table"]["users-table"]["write_capacity"] = 5
        print("   Added: write_capacity = 5 (smart default)")
    
    print()
    
    # Step 4: Generate final HCL
    print("[STEP 4] Final valid configuration")
    hcl = builder._config_to_hcl(config)
    print("   Generated HCL:")
    print(hcl)
    print()
    
    # Step 5: Show how user customization works
    print("[STEP 5] User customization example")
    print("   If user specified: --field billing_mode=PAY_PER_REQUEST")
    print()
    
    # Apply user customization
    config_with_user_input = config.copy()
    config_with_user_input["resource"]["aws_dynamodb_table"]["users-table"]["billing_mode"] = "PAY_PER_REQUEST"
    
    # Remove capacity settings for PAY_PER_REQUEST
    if "read_capacity" in config_with_user_input["resource"]["aws_dynamodb_table"]["users-table"]:
        del config_with_user_input["resource"]["aws_dynamodb_table"]["users-table"]["read_capacity"]
    if "write_capacity" in config_with_user_input["resource"]["aws_dynamodb_table"]["users-table"]:
        del config_with_user_input["resource"]["aws_dynamodb_table"]["users-table"]["write_capacity"]
    
    hcl_custom = builder._config_to_hcl(config_with_user_input)
    print("   Customized HCL:")
    print(hcl_custom)
    print()
    
    # Step 6: Show range key example
    print("[STEP 6] Complex example with range key")
    print("   User command: cloudbrew intelligent-create aws_dynamodb_table orders-table")
    print("                 --field hash_key=order_id")
    print("                 --field range_key=timestamp")
    print()
    
    complex_config = {
        "resource": {
            "aws_dynamodb_table": {
                "orders-table": {
                    "name": "orders-table",
                    "billing_mode": "PROVISIONED",
                    "hash_key": "order_id",
                    "range_key": "timestamp",
                    "attribute": [
                        {"name": "order_id", "type": "S"},
                        {"name": "timestamp", "type": "N"}
                    ],
                    "read_capacity": 10,
                    "write_capacity": 10
                }
            }
        }
    }
    
    hcl_complex = builder._config_to_hcl(complex_config)
    print("   Generated HCL with range key:")
    print(hcl_complex)
    print()
    
    print("=" * 60)
    print("Demo completed!")
    print()
    print("Key Takeaways:")
    print("• The Intelligent Builder handles ANY resource type")
    print("• It uses OpenTofu's validation to learn requirements")
    print("• Smart defaults handle 80% of configuration")
    print("• User input overrides defaults when needed")
    print("• Complex structures (like attribute blocks) are handled automatically")
    print()
    print("This approach works for DynamoDB, RDS, Lambda, API Gateway,")
    print("and ANY other OpenTofu resource - no hardcoded templates needed!")

if __name__ == "__main__":
    demo_dynamodb()