#!/usr/bin/env python3

"""
Test script to verify the CLI fixes work correctly.
"""

import sys
import os

# Add the current directory to Python path so we can import LCF modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_imports():
    """Test that all required imports work"""
    print("Testing imports...")
    try:
        from LCF.cli import parse_autoscale_config, create_resource
        print("✓ CLI imports successful")
        return True
    except Exception as e:
        print(f"✗ CLI import failed: {e}")
        return False

def test_autoscale_parsing():
    """Test the autoscale parsing function"""
    print("\nTesting autoscale parsing...")
    try:
        from LCF.cli import parse_autoscale_config
        
        # Test case 1: Basic format
        result1 = parse_autoscale_config("1:5@cpu:70,60")
        expected1 = {
            "min_size": 1,
            "max_size": 5,
            "metric": "cpu",
            "threshold": 70.0,
            "cooldown": 60
        }
        assert result1 == expected1, f"Expected {expected1}, got {result1}"
        print("✓ Basic autoscale parsing works")
        
        # Test case 2: Default cooldown
        result2 = parse_autoscale_config("2:10@memory:80")
        expected2 = {
            "min_size": 2,
            "max_size": 10,
            "metric": "memory",
            "threshold": 80.0,
            "cooldown": 300  # Default
        }
        assert result2 == expected2, f"Expected {expected2}, got {result2}"
        print("✓ Default cooldown autoscale parsing works")
        
        return True
    except Exception as e:
        print(f"✗ Autoscale parsing test failed: {e}")
        return False

def test_cli_help():
    """Test that CLI help works"""
    print("\nTesting CLI help...")
    try:
        # Test that we can at least import and access the CLI app
        from LCF.cli import app
        print("✓ CLI app accessible")
        
        # Check that the create command exists
        commands = [cmd.name for cmd in app.registered_commands]
        if 'create' in commands:
            print("✓ Create command registered")
        else:
            print("✗ Create command not found in registered commands")
            return False
            
        return True
    except Exception as e:
        print(f"✗ CLI help test failed: {e}")
        return False

def main():
    """Run all tests"""
    print("CloudBrew CLI Fix Test Suite")
    print("=" * 40)
    
    tests = [
        test_imports,
        test_autoscale_parsing,
        test_cli_help,
    ]
    
    results = []
    for test in tests:
        results.append(test())
    
    print("\n" + "=" * 40)
    print(f"Test Results: {sum(results)}/{len(results)} passed")
    
    if all(results):
        print("🎉 All tests passed! CLI fixes are working correctly.")
        return 0
    else:
        print("❌ Some tests failed. Please check the output above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())