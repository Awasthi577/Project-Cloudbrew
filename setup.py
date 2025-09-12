from setuptools import find_packages, setup

setup(
    name="cloudbrew",
    version="0.0.2",
    description="CloudBrew: vendor-neutral cloud orchestration CLI",
    author="Alakh Awasthi",
    packages=find_packages(exclude=("tests", "tests.*")),
    install_requires=[
        "typer>=0.9.0",
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
