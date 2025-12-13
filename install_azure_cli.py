import os
import urllib.request
import zipfile
import io
import shutil

# 1. Define the Installation Path (Matching your Debug Output)
TOFU_ROOT = r"C:\tmp\.cloudbrew_tofu"
INSTALL_DIR = os.path.join(TOFU_ROOT, "azure_cli")

print(f"--- Force Installer for Azure CLI ---")
print(f"Target Directory: {INSTALL_DIR}")

def install():
    # Clean up old attempts
    if os.path.exists(INSTALL_DIR):
        print("Cleaning up partial installation...")
        shutil.rmtree(INSTALL_DIR)
    
    os.makedirs(INSTALL_DIR, exist_ok=True)

    print("Downloading Azure CLI (approx 100MB)... please wait.")
    try:
        url = "https://aka.ms/installazurecliwindowszipx64"
        # Download and Extract in memory
        with urllib.request.urlopen(url) as response:
            print("Download complete. Extracting...")
            with zipfile.ZipFile(io.BytesIO(response.read())) as z:
                z.extractall(INSTALL_DIR)
    except Exception as e:
        print(f"[ERROR] Download failed: {e}")
        return

    # Verify Installation
    print("Verifying 'az.cmd'...")
    az_path = None
    for root, dirs, files in os.walk(INSTALL_DIR):
        if "az.cmd" in files:
            az_path = os.path.join(root, "az.cmd")
            break

    if az_path:
        print(f"\n[SUCCESS] Azure CLI installed at: {az_path}")
        print("You can now run 'cloudbrew tofu-apply' again.")
    else:
        print("\n[ERROR] Extraction finished, but 'az.cmd' was not found.")

if __name__ == "__main__":
    install()