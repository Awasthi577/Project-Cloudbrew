import os
import sys
import json
import logging

# Ensure we can import local LCF modules
sys.path.insert(0, os.getcwd())

def diagnose_config_loading():
    print("--- CLOUDBREW CONFIG DIAGNOSTIC ---")
    
    # 1. Locate Config File
    # CloudBrew looks in ~/.cloudbrew/config.json
    home = os.path.expanduser("~")
    config_path = os.path.join(home, ".cloudbrew", "config.json")
    
    print(f"[1] Checking Config Path: {config_path}")
    if not os.path.exists(config_path):
        print("    [FAIL] File does not exist!")
        return
    else:
        print("    [PASS] File exists.")

    # 2. Inspect Content (Safe Mode - masking secrets)
    print("\n[2] Reading Config Content...")
    try:
        with open(config_path, "r") as f:
            data = json.load(f)
            
        print(f"    Raw Keys: {list(data.keys())}")
        
        creds = data.get("creds", {})
        if "azure" not in creds:
            print("    [FAIL] 'azure' block missing in 'creds'!")
            print(f"    Found: {list(creds.keys())}")
            return
        
        azure_conf = creds["azure"]
        print("    [PASS] Azure block found.")
        print(f"    - Tenant ID: {azure_conf.get('tenant_id', 'MISSING')}")
        print(f"    - Client ID: {azure_conf.get('client_id', 'MISSING')}")
        
        # Check Secret Metadata
        meta = azure_conf.get("client_secret_meta", {})
        secret_key = meta.get("key")
        print(f"    - Secret Key Ref: {secret_key}")
        
    except Exception as e:
        print(f"    [FAIL] Could not read JSON: {e}")
        return

    # 3. Test Secret Retrieval (The likely failure point)
    print("\n[3] Testing Secret Decryption...")
    try:
        from LCF.secret_store import SecretStore
        store = SecretStore()
        
        if secret_key:
            secret_val = store.retrieve_secret(secret_key)
            if secret_val:
                masked = secret_val[:3] + "****" + secret_val[-3:] if len(secret_val) > 6 else "****"
                print(f"    [PASS] Successfully retrieved secret: {masked}")
            else:
                print(f"    [FAIL] Secret store returned None for key '{secret_key}'")
        else:
            print("    [FAIL] No secret key reference found in config.")
            
    except Exception as e:
        print(f"    [FAIL] Secret Store Error: {e}")

    # 4. Simulate Adapter Environment Injection
    print("\n[4] Simulating OpenTofu Adapter Injection...")
    try:
        # Simulate the logic inside _get_env
        env_simulation = {}
        if azure_conf.get("client_id"): env_simulation["ARM_CLIENT_ID"] = azure_conf["client_id"]
        if azure_conf.get("tenant_id"): env_simulation["ARM_TENANT_ID"] = azure_conf["tenant_id"]
        if azure_conf.get("subscription_id"): env_simulation["ARM_SUBSCRIPTION_ID"] = azure_conf["subscription_id"]
        if secret_val: env_simulation["ARM_CLIENT_SECRET"] = secret_val # Using retrieved value
        
        required = ["ARM_CLIENT_ID", "ARM_TENANT_ID", "ARM_SUBSCRIPTION_ID", "ARM_CLIENT_SECRET"]
        missing = [k for k in required if k not in env_simulation]
        
        if missing:
            print(f"    [FAIL] Injection would fail. Missing keys: {missing}")
        else:
            print("    [PASS] Injection logic is VALID. All keys present.")
            
    except Exception as e:
        print(f"    [FAIL] Simulation Error: {e}")

if __name__ == "__main__":
    diagnose_config_loading()