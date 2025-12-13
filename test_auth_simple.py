#!/usr/bin/env python3
"""
Simple authentication test for CloudBrew
"""

import sys
import os
sys.path.insert(0, '.')

def test_authentication():
    """Test authentication and credential loading"""
    print("Testing CloudBrew authentication...")
    
    # Test 1: Check if authenticated
    from LCF.auth_utils import is_authenticated_for_provider, get_authenticated_providers
    
    aws_auth = is_authenticated_for_provider("aws")
    gcp_auth = is_authenticated_for_provider("gcp")
    azure_auth = is_authenticated_for_provider("azure")
    
    print(f"AWS authenticated: {aws_auth}")
    print(f"GCP authenticated: {gcp_auth}")
    print(f"Azure authenticated: {azure_auth}")
    print(f"Authenticated providers: {get_authenticated_providers()}")
    
    # Test 2: Load credentials into environment
    from LCF.cloud_adapters.opentofu_adapter import OpenTofuAdapter
    print("\nLoading credentials via OpenTofu adapter...")
    ta = OpenTofuAdapter()
    
    # Check environment variables
    aws_key = os.environ.get("AWS_ACCESS_KEY_ID")
    aws_secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
    aws_region = os.environ.get("AWS_DEFAULT_REGION")
    
    print(f"AWS_ACCESS_KEY_ID: {'SET' if aws_key else 'NOT SET'}")
    print(f"AWS_SECRET_ACCESS_KEY: {'SET' if aws_secret else 'NOT SET'}")
    print(f"AWS_DEFAULT_REGION: {aws_region if aws_region else 'NOT SET'}")
    
    # Test 3: Test AWS connection
    if aws_key and aws_secret:
        try:
            import boto3
            print("\nTesting AWS connection...")
            sts = boto3.client('sts')
            identity = sts.get_caller_identity()
            print(f"AWS connection successful!")
            print(f"Account: {identity['Account']}")
            print(f"User ID: {identity['UserId']}")
            print(f"ARN: {identity['Arn']}")
            return True
        except Exception as e:
            print(f"AWS connection failed: {e}")
            return False
    else:
        print("AWS credentials not available for testing")
        return aws_auth  # Return True if authenticated, even if we can't test connection

if __name__ == "__main__":
    success = test_authentication()
    if success:
        print("\nAuthentication test PASSED!")
        sys.exit(0)
    else:
        print("\nAuthentication test FAILED!")
        sys.exit(1)