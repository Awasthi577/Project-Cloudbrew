#!/usr/bin/env python3
"""
Test S3 bucket creation with CloudBrew
"""

import sys
import os
import time
sys.path.insert(0, '.')

def test_s3_creation():
    """Test S3 bucket creation end-to-end"""
    print("Testing S3 bucket creation...")
    
    # First, check if we have AWS credentials
    from LCF.auth_utils import is_authenticated_for_provider
    if not is_authenticated_for_provider("aws"):
        print("❌ Not authenticated for AWS. Run 'cloudbrew init' first.")
        return False
    
    print("Authenticated for AWS")
    
    # Test credential loading
    from LCF.cloud_adapters.opentofu_adapter import OpenTofuAdapter
    ta = OpenTofuAdapter()
    
    # Check environment variables
    aws_key = os.environ.get("AWS_ACCESS_KEY_ID")
    aws_secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
    aws_region = os.environ.get("AWS_DEFAULT_REGION")
    
    print(f"AWS_ACCESS_KEY_ID: {'SET' if aws_key else 'NOT SET'}")
    print(f"AWS_SECRET_ACCESS_KEY: {'SET' if aws_secret else 'NOT SET'}")
    print(f"AWS_DEFAULT_REGION: {aws_region if aws_region else 'NOT SET'}")
    
    if not aws_key or not aws_secret:
        print("AWS credentials not loaded into environment")
        return False
    
    print("AWS credentials loaded")
    
    # Test boto3 connection
    try:
        import boto3
        sts = boto3.client('sts')
        identity = sts.get_caller_identity()
        print(f"AWS connection successful: {identity['Account']}")
    except Exception as e:
        print(f"AWS connection failed: {e}")
        return False
    
    # Generate unique bucket name
    bucket_name = f"cloudbrew-test-bucket-{int(time.time())}"
    
    # Create S3 bucket specification
    spec = {
        'name': 'test-bucket',
        'type': 'aws_s3_bucket',
        'bucket': bucket_name,
        'acl': 'private',
        'region': 'us-east-1',
        'provider': 'aws'
    }
    
    print(f"Creating S3 bucket: {bucket_name}")
    
    try:
        # Create the bucket
        result = ta.create_instance('test-bucket', spec, plan_only=False)
        
        if result.get('success'):
            print("S3 bucket creation initiated successfully")
            
            # Verify bucket exists
            try:
                s3 = boto3.client('s3')
                # Wait a moment for bucket to be created
                time.sleep(2)
                
                # Check if bucket exists
                response = s3.list_buckets()
                bucket_names = [b['Name'] for b in response.get('Buckets', [])]
                
                if bucket_name in bucket_names:
                    print(f"S3 bucket '{bucket_name}' created successfully!")
                    
                    # Clean up - delete the test bucket
                    print("Cleaning up test bucket...")
                    s3.delete_bucket(Bucket=bucket_name)
                    print(f"Test bucket '{bucket_name}' deleted")
                    return True
                else:
                    print(f"S3 bucket '{bucket_name}' not found after creation")
                    print("Available buckets:", bucket_names)
                    return False
                    
            except Exception as e:
                print(f"Error verifying bucket: {e}")
                return False
        else:
            print(f"S3 bucket creation failed: {result.get('error', 'Unknown error')}")
            return False
            
    except Exception as e:
        print(f"❌ Exception during S3 bucket creation: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_s3_creation()
    if success:
        print("\nS3 bucket creation test PASSED!")
        sys.exit(0)
    else:
        print("\nS3 bucket creation test FAILED!")
        sys.exit(1)