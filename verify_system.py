import os
import shutil
import logging
from LCF.cloud_adapters.opentofu_adapter import OpenTofuAdapter

# Configure logging to see the schema fetch process
logging.basicConfig(level=logging.INFO)

def verify_system():
    print(">>> 1. SYSTEM CHECK...")
    
    # 1. Verify versions.tf location
    v_path = ".cloudbrew_tofu/versions.tf"
    if not os.path.exists(v_path):
        print(f"[ERROR] Missing file: {v_path}")
        print("Please move your versions.tf file into the .cloudbrew_tofu/ folder.")
        return

    # 2. Clear stale cache if it exists (Optional, but safer)
    if os.path.exists("schema_cache.json"):
        print(">>> Note: schema_cache.json found. If you see errors, delete this file and re-run.")

    print("\n>>> 2. INITIALIZING ADAPTER...")
    # This triggers the SchemaManager
    try:
        adapter = OpenTofuAdapter()
    except Exception as e:
        print(f"[CRITICAL] Adapter failed to init: {e}")
        return

    print("\n>>> 3. GENERATING COMPLEX RESOURCE (AWS S3)...")
    spec = {
        "provider": "aws",
        "type": "aws_s3_bucket",
        "bucket": "my-deep-schema-bucket",
        "tags": {
            "Environment": "Production", # Should be Map (key = value)
            "Owner": "DevOps"
        },
        "website": {
            "index_document": "index.html", # Should be Block (key { ... })
            "error_document": "error.html"
        },
        "versioning": {
            "enabled": True # Should be Block
        }
    }

    result = adapter.plan("verify-bucket", spec)
    
    if result.get("success"):
        print("\n[SUCCESS] HCL Generated Successfully!")
        
        # Print the HCL for manual verification
        tf_file = ".cloudbrew_tofu/verify-bucket/main.tf"
        if os.path.exists(tf_file):
            print(f"\n--- FILE: {tf_file} ---")
            with open(tf_file, "r") as f:
                content = f.read()
                print(content)
            print("-----------------------------------")
            
            # Auto-Verification Logic
            if 'tags = {' in content and 'website {' in content:
                 print("✅ VERIFIED: Maps and Blocks are distinguished correctly.")
            else:
                 print("❌ WARNING: HCL structure looks incorrect. Check the output above.")
    else:
        print(f"\n[FAILURE] {result.get('error')}")

if __name__ == "__main__":
    verify_system()