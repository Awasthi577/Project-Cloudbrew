#!/usr/bin/env python3
"""
Test script for the Intelligent Configuration Builder
Demonstrates how it can automatically build valid OpenTofu configurations
with minimal user input.
"""

import sys
import os

# Add the Cloudbrew directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from LCF.intelligent_builder import IntelligentBuilder

def test_builder():
    """Test the intelligent builder with various resource types"""
    
    builder = IntelligentBuilder()
    
    print("CloudBrew Intelligent Configuration Builder Test")
    print("=" * 60)
    
    # Test 1: AWS Instance
    print("\n[1/4] Testing AWS Instance...")
    try:
        config = builder.build_configuration("aws_instance", {
            "instance_type": "t3.micro"
        })
        hcl = builder._config_to_hcl(config)
        print("[SUCCESS] Generated AWS Instance config:")
        print(hcl)
    except Exception as e:
        print(f"[FAILED] {e}")
    
    # Test 2: S3 Bucket
    print("\n[2/4] Testing S3 Bucket...")
    try:
        config = builder.build_configuration("aws_s3_bucket", {
            "bucket": "my-test-bucket-12345"
        })
        hcl = builder._config_to_hcl(config)
        print("[SUCCESS] Generated S3 Bucket config:")
        print(hcl)
    except Exception as e:
        print(f"[FAILED] {e}")
    
    # Test 3: RDS Instance
    print("\n[3/4] Testing RDS Instance...")
    try:
        config = builder.build_configuration("aws_db_instance", {
            "engine": "postgres",
            "instance_class": "db.t3.micro"
        })
        hcl = builder._config_to_hcl(config)
        print("[SUCCESS] Generated RDS Instance config:")
        print(hcl)
    except Exception as e:
        print(f"[FAILED] {e}")
    
    # Test 4: Minimal input (let builder figure out everything)
    print("\n[4/4] Testing with minimal input...")
    try:
        config = builder.build_configuration("aws_instance", {})
        hcl = builder._config_to_hcl(config)
        print("[SUCCESS] Generated config with smart defaults:")
        print(hcl)
    except Exception as e:
        print(f"[FAILED] {e}")
    
    print("\n" + "=" * 60)
    print("Test completed!")

if __name__ == "__main__":
    test_builder()