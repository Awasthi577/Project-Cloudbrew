#!/usr/bin/env python3
"""
Debug script to see what OpenTofu errors we're getting for DynamoDB
"""

import sys
import os
import subprocess
import tempfile

# Add the Cloudbrew directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_dynamodb_validation():
    """Test OpenTofu validation for DynamoDB"""
    
    print("Testing OpenTofu validation for DynamoDB")
    print("=" * 50)
    
    # Test minimal configuration
    minimal_config = '''resource "aws_dynamodb_table" "test" {
  # Empty - see what OpenTofu complains about
}
'''
    
    print("\n[1] Testing minimal configuration:")
    print(minimal_config)
    
    # Write to temp file and validate
    with tempfile.NamedTemporaryFile(mode='w', suffix='.tf', delete=False) as f:
        f.write(minimal_config)
        temp_file = f.name
    
    try:
        result = subprocess.run(
            ['tofu', 'validate', temp_file],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        print(f"\nReturn code: {result.returncode}")
        print(f"STDOUT:\n{result.stdout}")
        print(f"STDERR:\n{result.stderr}")
        
    except subprocess.TimeoutExpired:
        print("Validation timed out")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        os.unlink(temp_file)
    
    # Test with some fields
    partial_config = '''resource "aws_dynamodb_table" "test" {
  name = "test-table"
  billing_mode = "PROVISIONED"
  hash_key = "id"
}
'''
    
    print("\n" + "=" * 50)
    print("\n[2] Testing partial configuration:")
    print(partial_config)
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.tf', delete=False) as f:
        f.write(partial_config)
        temp_file = f.name
    
    try:
        result = subprocess.run(
            ['tofu', 'validate', temp_file],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        print(f"\nReturn code: {result.returncode}")
        print(f"STDOUT:\n{result.stdout}")
        print(f"STDERR:\n{result.stderr}")
        
    except subprocess.TimeoutExpired:
        print("Validation timed out")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        os.unlink(temp_file)
    
    # Test with attributes
    with_attributes = '''resource "aws_dynamodb_table" "test" {
  name = "test-table"
  billing_mode = "PROVISIONED"
  hash_key = "id"
  read_capacity = 5
  write_capacity = 5
  
  attribute {
    name = "id"
    type = "S"
  }
}
'''
    
    print("\n" + "=" * 50)
    print("\n[3] Testing with attributes:")
    print(with_attributes)
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.tf', delete=False) as f:
        f.write(with_attributes)
        temp_file = f.name
    
    try:
        result = subprocess.run(
            ['tofu', 'validate', temp_file],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        print(f"\nReturn code: {result.returncode}")
        print(f"STDOUT:\n{result.stdout}")
        print(f"STDERR:\n{result.stderr}")
        
    except subprocess.TimeoutExpired:
        print("Validation timed out")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        os.unlink(temp_file)

if __name__ == "__main__":
    test_dynamodb_validation()