import os
import shutil

# 1. Define Paths
cache_file = "schema_cache.json"
win_tofu_root = r"C:\tmp\.cloudbrew_tofu"

print(">>> 1. CLEANING UP OLD DATA...")

# Remove the stale cache file
if os.path.exists(cache_file):
    try:
        os.remove(cache_file)
        print(f"✅ Deleted stale cache: {cache_file}")
    except Exception as e:
        print(f"⚠️ Could not delete {cache_file}: {e}")
else:
    print(f"   (No {cache_file} found, that's good)")

# Clean the Windows Temp directory
if os.path.exists(win_tofu_root):
    try:
        shutil.rmtree(win_tofu_root)
        print(f"✅ Cleaned temp dir: {win_tofu_root}")
    except Exception as e:
        print(f"⚠️ Could not delete temp dir: {e}")

# Re-create the temp directory
os.makedirs(win_tofu_root, exist_ok=True)


print("\n>>> 2. SETTING UP PROVIDERS...")

# Write versions.tf to the correct Windows location
versions_content = """terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}
"""

v_path = os.path.join(win_tofu_root, "versions.tf")
with open(v_path, "w") as f:
    f.write(versions_content)

print(f"✅ Created: {v_path}")
print("\n>>> READY.")
print("Now run 'python verify_system.py'. It will take ~30-60s to download schemas.")