#!/usr/bin/env python3
"""
debug_resolver.py

Quick diagnostic script for CloudBrew resolver + adapter.

Usage:
  python debug_resolver.py <resource_word> [--provider aws|azure|google|auto]

What it does (read-only):
 - Prints environment hints (CLOUDBREW_TOFU_ROOT, CLOUDBREW_OPENTOFU_BIN)
 - Shows which templates and mapping keys are available
 - Shows the tofu/opentofu binary path and attempts `tofu providers schema -json` (only to check availability)
 - Imports ResourceResolver and attempts to resolve the given resource word (auto and for specific providers)
 - Prints top fuzzy candidates if available
 - If OpenTofuAdapter is importable, generates HCL preview (no file writes, no apply)
"""

import os
import sys
import json
import shutil
import subprocess
from pprint import pprint

RESOURCE = sys.argv[1] if len(sys.argv) > 1 else "redis"
PROVIDER_HINT = None
if len(sys.argv) > 2:
    PROVIDER_HINT = sys.argv[2].lower()

print("=== CloudBrew Resolver Debugger ===")
print(f"Resource to test: {RESOURCE}")
if PROVIDER_HINT:
    print(f"Provider hint: {PROVIDER_HINT}")
print()

# 1) environment
print("-> Environment variables (relevant)")
env_keys = ["CLOUDBREW_TOFU_ROOT", "CLOUDBREW_OPENTOFU_BIN", "PATH"]
for k in env_keys:
    print(f"{k} = {os.environ.get(k)!r}")
print()

# 2) tofu binary check
def find_tofu():
    candidates = [
        os.environ.get("CLOUDBREW_OPENTOFU_BIN"),
        shutil.which("tofu"),
        shutil.which("opentofu"),
    ]
    return next((c for c in candidates if c), None)

tofu_bin = find_tofu()
print("-> OpenTofu (tofu/opentofu) binary detection")
print("Detected tofu binary:", tofu_bin)
if not tofu_bin:
    print("  WARNING: 'tofu' binary not found on PATH and CLOUDBREW_OPENTOFU_BIN not set.")
else:
    # Check providers schema availability (do not fail if command fails)
    try:
        print("  Running: tofu providers schema -json (timeout 10s)...")
        proc = subprocess.run([tofu_bin, "providers", "schema", "-json"],
                              capture_output=True, text=True, timeout=10)
        print(f"  Return code: {proc.returncode}; stdout bytes: {len(proc.stdout)}; stderr bytes: {len(proc.stderr)}")
        if proc.returncode == 0:
            # print a tiny snippet
            try:
                parsed = json.loads(proc.stdout)
                print("  providers schema JSON parsed. Top-level keys count:", len(parsed.keys()) if isinstance(parsed, dict) else "n/a")
            except Exception as e:
                print("  Could not parse schema JSON:", e)
        else:
            print("  providers schema command failed (stderr snippet):")
            print(proc.stderr.strip()[:800])
    except Exception as e:
        print("  providers schema command invocation error:", e)
print()

# 3) attempt to import and inspect ResourceResolver
print("-> ResourceResolver static mappings & resolve attempts")
try:
    from LCF.resource_resolver import ResourceResolver
    rr = ResourceResolver()
    # show mapping keys loaded (first 120)
    keys = sorted(list(rr.static_registry.keys()))
    print(f"  Number of loaded static mapping keys: {len(keys)}")
    print("  Sample mapping keys (first 120):")
    pprint(keys[:120])
    print()
    # Try resolve with provider hint and auto
    def try_resolve(word, provider=None):
        try:
            out = rr.resolve(resource=word, provider=provider or "auto")
            return out
        except Exception as ex:
            return {"error": str(ex)}
    print("  Resolve (auto):")
    pprint(try_resolve(RESOURCE, None))
    if PROVIDER_HINT:
        print(f"  Resolve ({PROVIDER_HINT}):")
        pprint(try_resolve(RESOURCE, PROVIDER_HINT))
    else:
        # try common providers to get more insight
        for p in ("aws", "azurerm", "google"):
            print(f"  Resolve ({p}):")
            pprint(try_resolve(RESOURCE, p))
    print()
except Exception as e:
    print("  Failed to import/initialize ResourceResolver:", e)
print()

# 4) list templates directory (where Jinja templates are kept)
print("-> Templates directory (searching for *.tf.j2)")
try:
    # Try to find the package base and templates folder
    import inspect
    import LCF
    base = os.path.dirname(inspect.getsourcefile(LCF))
    templates_dir = os.path.join(base, "templates")
    print("  guessed templates_dir:", templates_dir)
    if os.path.isdir(templates_dir):
        tfiles = [f for f in os.listdir(templates_dir) if f.endswith(".tf.j2")]
        print("  Found template files:", tfiles)
    else:
        print("  templates directory not found at guessed location.")
except Exception as e:
    print("  Could not locate templates directory automatically:", e)
print()

# 5) show mappings directory contents (if present)
print("-> MAPPINGS directory search (LCF/mappings)")
try:
    import inspect, LCF
    base = os.path.dirname(inspect.getsourcefile(LCF))
    mappings_dir = os.path.join(base, "mappings")
    print("  guessed mappings_dir:", mappings_dir)
    if os.path.isdir(mappings_dir):
        files = [f for f in os.listdir(mappings_dir) if f.endswith(".json")]
        print("  JSON mapping files found:", files)
        # show files that might match our resource name
        matches = [f for f in files if RESOURCE.lower() in f.lower()]
        print("  Files with resource-name-like substrings:", matches)
        # print first 2 mapping files content for quicker debug
        for f in files[:5]:
            path = os.path.join(mappings_dir, f)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                print(f"  --- {f} keys: {list(data.keys())[:12]}")
            except Exception as exc:
                print(f"  --- {f} read error: {exc}")
    else:
        print("  mappings directory not found at guessed location.")
except Exception as e:
    print("  Could not inspect mappings directory:", e)
print()

# 6) attempt to import the adapter and render an HCL preview (no write)
print("-> OpenTofuAdapter HCL preview (no apply)")
try:
    from LCF.cloud_adapters.opentofu_adapter import OpenTofuAdapter
    ta = OpenTofuAdapter()
    # Build a safe spec that mirrors likely user input (no destructive actions)
    sample_spec = {
        "type": RESOURCE if RESOURCE.startswith(("aws_", "azurerm_", "google_")) else "null_resource",
        "provider": PROVIDER_HINT or "aws",
        "name": f"dbg_{RESOURCE}",
        # add common friendly aliases to see if normalizer works
        "image": "ubuntu-22.04",
        "size": "small",
        "tags": {"Environment": "Dev", "ManagedBy": "CloudBrew"},
        "region": "us-east-1"
    }
    hcl = ta._generate_hcl(f"dbg-{RESOURCE}", sample_spec)
    print("  HCL preview (first 400 chars):")
    print(hcl[:400])
    print("\n  HCL preview tail (last 400 chars):")
    print(hcl[-400:])
except Exception as e:
    print("  Could not create adapter HCL preview:", e)
print()

# 7) helpful hints
print("-> Helpful next steps / checks")
print(" - If resolve() returned empty or errors: add a mapping in LCF/mappings for the resource,")
print("   or ensure 'tofu' is installed so schema bootstrap can work.")
print(" - To test schema bootstrap manually:")
if tofu_bin:
    print(f"   {tofu_bin} providers schema -json | head -c 200")
else:
    print("   install 'tofu' or set CLOUDBREW_OPENTOFU_BIN to its path")
print()
print("Done.")
