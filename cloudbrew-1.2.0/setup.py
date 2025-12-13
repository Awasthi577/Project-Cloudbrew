from setuptools import find_packages, setup
from pathlib import Path

# Fix: Use Path(__file__).parent to safely locate README.md regardless of where the command is run
this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text(encoding="utf-8")

setup(
    name="cloudbrew",
    version="1.2.0",
    description="CloudBrew: vendor-neutral cloud orchestration CLI",
    long_description=long_description,  # Uses the variable defined above
    long_description_content_type="text/markdown",
    author="Alakh Awasthi",
    packages=find_packages(exclude=("tests", "tests.*")),
    install_requires=[
        "typer>=0.9.0",
        "requests>=2.28.0",
        "jinja2",           # Required for StackManager
        "keyring",          # <--- Added
        "boto3",            # <--- Added
        "google-auth",      # <--- Added
        "google-api-python-client", # <--- Added
        "azure-identity",   # <--- Added
    ],
    extras_require={
        "cloud-creds": [
            "keyring>=23.0.0",
            "cryptography>=40.0.0",
            "boto3>=1.20.0",
            "google-auth>=2.0.0",
            "google-api-python-client>=2.0.0",
            "azure-identity>=1.12.0",
        ],
        "dev": [
            "pytest",
            "pytest-cov",
        ],
    },
    entry_points={
        "console_scripts": [
            "cloudbrew = LCF.cli:app",
            "cloudbrew-init = LCF.cli_init:app",
        ],
    },
    python_requires=">=3.9",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
    ],
)