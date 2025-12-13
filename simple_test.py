#!/usr/bin/env python3

"""
Simple test script to verify the CLI fixes work correctly.
"""

import sys
import os

# Add the current directory to Python path so we can import LCF modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def main():
    """Run simple tests"""
    print("CloudBrew CLI Fix Test")
    print("=" * 30)
    
    try:
        # Test 1: Import the CLI module
        print("Testing CLI import...")
        from LCF.cli import parse_autoscale_config, app
        print("SUCCESS: CLI imports work")
        
        # Test 2: Test autoscale parsing
        print("\nTesting autoscale parsing...")
        result = parse_autoscale_config("1:5@cpu:70,60")
        expected = {
            "min_size": 1,
            "max_size": 5,
            "metric": "cpu",
            "threshold": 70.0,
            "cooldown": 60
        }
        if result == expected:
            print("SUCCESS: Autoscale parsing works")
        else:
            print(f"FAIL: Expected {expected}, got {result}")
            return 1
        
        # Test 3: Check that create command exists
        print("\nTesting create command registration...")
        commands = [cmd.name for cmd in app.registered_commands]
        if 'create' in commands:
            print("SUCCESS: Create command is registered")
        else:
            print("FAIL: Create command not found")
            print(f"Available commands: {commands}")
            return 1
        
        print("\n" + "=" * 30)
        print("All tests passed! CLI fixes are working correctly.")
        return 0
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())