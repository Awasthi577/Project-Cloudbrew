#!/usr/bin/env python3
"""
fix_schema3_fixed.py

Bootstraps provider folders, runs `tofu init`, and caches provider schemas.
Works on Windows, macOS, Linux. Avoids Unicode decode errors on Windows by forcing UTF-8
and replacing undecodable bytes.
"""

import subprocess
from pathlib import Path
import sys

# Providers to bootstrap
PROVIDERS = ["aws", "azurerm", "google"]

ROOT = Path(".")
PROV_ROOT = ROOT / ".cloudbrew_providers"
SCHEMA_CACHE = ROOT / ".cloudbrew_cache" / "schema"

PROV_ROOT.mkdir(parents=True, exist_ok=True)
SCHEMA_CACHE.mkdir(parents=True, exist_ok=True)

VERSIONS_TF = {
    "aws": """terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}
""",
    "azurerm": """terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 3.0"
    }
  }
}
""",
    "google": """terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 4.0"
    }
  }
}
""",
}

# provider.tf contents: note azurerm is multi-line
PROVIDER_TF = {
    "aws": 'provider "aws" { region = "us-east-1" }\n',
    "azurerm": 'provider "azurerm" {\n  features {}\n}\n',
    "google": 'provider "google" { region = "us-central1" }\n',
}

def run_cmd(cmd, cwd):
    """
    Run subprocess and return CompletedProcess.
    Use encoding='utf-8' and errors='replace' to avoid decode errors on Windows.
    Do not raise on non-zero exit; caller will check returncode.
    """
    print(f"\n➡️ Running: {' '.join(cmd)} (cwd={cwd})")
    try:
        # capture combined stdout+stderr so we can log it
        cp = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        # print first lines of output for visibility (but not too long)
        out_preview = cp.stdout[:4000] if cp.stdout else ""
        print(out_preview + ("\n... (truncated)\n" if cp.stdout and len(cp.stdout) > 4000 else ""))
        if cp.returncode != 0:
            print(f"⚠️ Command exited with code {cp.returncode}")
        return cp
    except FileNotFoundError as e:
        print(f"❌ Command not found: {cmd[0]} -- make sure `tofu` is on PATH.")
        raise

def main():
    for p in PROVIDERS:
        print(f"\n=== Bootstrapping provider: {p} ===")
        d = PROV_ROOT / p
        d.mkdir(parents=True, exist_ok=True)

        # Write versions.tf
        (d / "versions.tf").write_text(VERSIONS_TF[p], encoding="utf-8")

        # Write provider.tf (azurerm now multi-line)
        (d / "provider.tf").write_text(PROVIDER_TF[p], encoding="utf-8")

        # Run tofu init
        init_cp = run_cmd(["tofu", "init", "-no-color"], cwd=str(d))

        # If init failed, show a short hint but continue to attempt schema extraction (sometimes schema still works)
        if init_cp.returncode != 0:
            print(f"⚠️ tofu init failed for {p} (exit {init_cp.returncode}). See output above.")

        # Extract schema JSON
        print("➡️ Extracting schema...")
        schema_file = SCHEMA_CACHE / f"{p}-schema.json"

        schema_cp = run_cmd(["tofu", "providers", "schema", "-json"], cwd=str(d))

        if schema_cp.returncode == 0 and schema_cp.stdout:
            # schema_cp.stdout is str because we used encoding='utf-8'
            try:
                schema_file.write_text(schema_cp.stdout, encoding="utf-8")
                print(f"✅ Schema saved: {schema_file}")
            except Exception as e:
                print(f"❌ Failed to write schema file: {e}")
        else:
            # Provide a helpful message and save whatever output we have (if any) to a .log for debugging
            debug_log = SCHEMA_CACHE / f"{p}-schema-extraction.log"
            debug_text = schema_cp.stdout if schema_cp.stdout else "<no output captured>"
            try:
                debug_log.write_text(debug_text, encoding="utf-8")
                print(f"⚠️ Failed to extract schema for {p}. Raw output saved to {debug_log}")
            except Exception as e:
                print(f"❌ Could not write debug log: {e}")

    print("\n🎉 Done! Providers bootstrapped and schema extraction attempted.")
    print(f"Look in: {PROV_ROOT}  and {SCHEMA_CACHE}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user. Exiting.")
        sys.exit(1)
