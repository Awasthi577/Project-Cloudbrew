#!/usr/bin/env python3
"""
Debug S3 HCL generation
"""

import sys
sys.path.insert(0, '.')

def debug_s3_hcl_generation():
    """Debug the S3 bucket HCL generation process"""
    from LCF.cloud_adapters.opentofu_adapter import OpenTofuAdapter
    
    print("Debugging S3 bucket HCL generation...")
    
    # Create adapter
    ta = OpenTofuAdapter()
    
    # Test specification
    spec = {
        'name': 'my-bucket',
        'type': 'aws_s3_bucket',
        'bucket': 'my-app-bucket',
        'acl': 'private',
        'versioning': {'enabled': False},
        'region': 'us-east-1',
        'provider': 'aws'
    }
    
    print("Input spec:", spec)
    
    # Check if schema is available
    try:
        schema = ta.schema_mgr.get('aws_s3_bucket')
        print("Schema found: YES")
        
        block = schema.get('block', {})
        attrs = block.get('attributes', {})
        blocks = block.get('block_types', {})
        
        print(f"Attributes: {list(attrs.keys())}")
        print(f"Block types: {list(blocks.keys())}")
        
        # Check if versioning is in block types
        if 'versioning' in blocks:
            print("✅ versioning is defined as a block type in schema")
        elif 'versioning' in attrs:
            print("❌ versioning is defined as an attribute in schema")
        else:
            print("? versioning not found in schema")
            
    except Exception as e:
        print(f"Schema found: NO ({e})")
        schema = None
    
    # Generate HCL using the same method as the actual creation
    print("\nGenerating HCL...")
    hcl = ta._generate_hcl('my-bucket', spec)
    
    print("Generated HCL:")
    print(hcl)
    
    # Check the result
    if 'versioning {' in hcl:
        print("\nSUCCESS: HCL contains proper versioning block")
        return True
    elif 'versioning = {' in hcl:
        print("\nFAILED: HCL contains versioning as attribute")
        return False
    else:
        print("\nUNKNOWN: versioning not found in HCL")
        return False

if __name__ == "__main__":
    success = debug_s3_hcl_generation()
    if not success:
        sys.exit(1)