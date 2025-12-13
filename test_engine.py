from LCF.resource_resolver import ResourceResolver
import os
import json

# Fix: Import the global variable directly from the module if needed, 
# or just re-derive the path to be safe for this test.
MAPPINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "LCF", "mappings")

print("--- DIAGNOSTIC START ---")

# 1. Initialize Resolver (this will trigger your internal Debug prints)
rr = ResourceResolver()

# 2. Check Directory Content (Manual Check)
print(f"\n[Test Script] Checking Path: {MAPPINGS_PATH}")
if os.path.exists(MAPPINGS_PATH):
    files = os.listdir(MAPPINGS_PATH)
    print(f"[Test Script] Files found: {files}")
else:
    print("[Test Script] CRITICAL: Directory not found on disk.")

# 3. Check what actually got loaded into memory
print(f"\n[Registry Content] Keys loaded: {list(rr.static_registry.keys())}")

if "vm" in rr.static_registry:
    print("[Registry Content] 'vm' entry found!")
    print(json.dumps(rr.static_registry["vm"], indent=2))
else:
    print("[Registry Content] CRITICAL: 'vm' key is MISSING from registry.")

# 4. Simulate the CLI resolution
print("\n[Resolution Test] Resolving 'vm' with provider 'aws'...")
res = rr.resolve("vm", "aws")
print(f"Result: {json.dumps(res, indent=2)}")

print("--- DIAGNOSTIC END ---")