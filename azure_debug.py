import os
import sys
import json

# Force current directory to be top of path
sys.path.insert(0, os.getcwd())

def debug_config():
    print("--- CLOUDBREW PROJECT ID DEBUGGER ---")
    
    try:
        from LCF.bootstrap import load_config
        print("[1] Importing load_config... SUCCESS")
    except ImportError as e:
        print(f"[1] Importing load_config... FAILED: {e}")
        return

    # Call load_config exactly like the adapter does
    try:
        cfg = load_config()
        print(f"[2] Calling load_config()... Returned type: {type(cfg)}")
    except Exception as e:
        print(f"[2] Calling load_config()... CRASHED: {e}")
        return

    if not cfg:
        print("[FAIL] Config is empty or None.")
        return

    gcp = cfg.get("creds", {}).get("gcp", {})
    project = gcp.get("project")
    
    print(f"[3] GCP Block Found: {bool(gcp)}")
    print(f"[4] Project ID Found: '{project}'")
    
    if project:
        print("\n[SUCCESS] The config system is working correctly.")
        print("If deployment still fails, the Adapter code is swallowing this value.")
    else:
        print("\n[FAIL] Project ID is missing from the loaded config.")
        print("Please run 'cloudbrew init' again and ensure you type the project ID.")

if __name__ == "__main__":
    debug_config()