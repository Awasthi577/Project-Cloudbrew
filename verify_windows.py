import os
import shutil
import sys

# 1. Determine where the Adapter is actually writing
# (Mirroring the logic from opentofu_adapter.py)
TOFU_ROOT = r"C:\tmp\.cloudbrew_tofu"
print(f"DEBUG: On Windows, Adapter uses: {TOFU_ROOT}")

# 2. Fix versions.tf location
# The SchemaManager looks in TOFU_ROOT, so versions.tf MUST be there.
if not os.path.exists(TOFU_ROOT):
    os.makedirs(TOFU_ROOT)

src_version = ".cloudbrew_tofu/versions.tf"
dst_version = os.path.join(TOFU_ROOT, "versions.tf")

if os.path.exists(src_version):
    print(f"Copying versions.tf to {dst_version}...")
    shutil.copy(src_version, dst_version)
else:
    print(f"[WARNING] Could not find {src_version}. Schema fetching might fail.")

# 3. Read the generated file
# The previous run already generated the file, let's just find it.
target_file = os.path.join(TOFU_ROOT, "verify-bucket", "main.tf")

print(f"\nChecking for file: {target_file}")

if os.path.exists(target_file):
    print("\n" + "="*40)
    print("      GENERATED HCL OUTPUT      ")
    print("="*40)
    with open(target_file, "r") as f:
        content = f.read()
        print(content)
    print("="*40)
    
    # 4. Logic Check
    print("\n>>> ANALYSIS:")
    
    # Check TAGS (Should be Map)
    if 'tags = {' in content:
        print("✅ TAGS are correct (Found '=' sign).")
    else:
        print("❌ TAGS are wrong (Missing '=' sign).")

    # Check WEBSITE (Should be Block)
    if 'website {' in content and 'website = {' not in content:
        print("✅ WEBSITE is correct (No '=' sign).")
    elif 'website = {' in content:
        print("❌ WEBSITE is wrong (Found '=' sign). Schema lookup failed.")
    else:
        print("⚠️  WEBSITE block not found.")

else:
    print("[ERROR] File not found. Try running 'python verify_system.py' again now that versions.tf is fixed.")