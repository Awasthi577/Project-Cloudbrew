#!/usr/bin/env python3
"""
Test the versioning fix
"""

import sys
sys.path.insert(0, '.')

def test_versioning_rendering():
    """Test that versioning is rendered as a block, not an attribute"""
    from LCF.cloud_adapters.opentofu_adapter import OpenTofuAdapter
    
    print("Testing versioning HCL rendering...")
    
    # Create adapter
    ta = OpenTofuAdapter()
    
    # Test the _render_hcl_field_python method directly
    key = "versioning"
    value = {"enabled": True}
    schema = None  # No schema needed for this test
    depth = 1
    
    result = ta._render_hcl_field_python(key, value, schema, depth)
    
    print("Versioning rendering result:")
    print(repr(result))
    print("\nActual output:")
    print(result)
    
    # Check if it's rendered as a block
    if "versioning {" in result and "enabled = true" in result:
        print("\nSUCCESS: Versioning is rendered as a proper Terraform block!")
        return True
    else:
        print("\nFAILED: Versioning is not rendered correctly")
        return False

def test_s3_bucket_hcl():
    """Test complete S3 bucket HCL generation"""
    from LCF.cloud_adapters.opentofu_adapter import OpenTofuAdapter
    
    print("\n" + "="*50)
    print("Testing complete S3 bucket HCL generation...")
    
    ta = OpenTofuAdapter()
    
    spec = {
        'name': 'test-bucket',
        'type': 'aws_s3_bucket',
        'bucket': 'test-bucket-name',
        'acl': 'private',
        'versioning': {'enabled': True},
        'region': 'us-east-1',
        'provider': 'aws'
    }
    
    try:
        hcl = ta._generate_hcl('test-bucket', spec)
        print("Generated HCL:")
        print(hcl)
        
        # Check for proper versioning block
        if "versioning {" in hcl and "enabled = true" in hcl:
            print("\nSUCCESS: S3 bucket HCL includes proper versioning block!")
            return True
        else:
            print("\nFAILED: S3 bucket HCL does not include proper versioning block")
            return False
            
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success1 = test_versioning_rendering()
    success2 = test_s3_bucket_hcl()
    
    if success1 and success2:
        print("\n" + "="*50)
        print("ALL TESTS PASSED! Versioning fix is working!")
        sys.exit(0)
    else:
        print("\n" + "="*50)
        print("SOME TESTS FAILED!")
        sys.exit(1)