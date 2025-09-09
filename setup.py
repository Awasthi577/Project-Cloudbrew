from setuptools import find_packages, setup

setup(
    name="cloudbrew",
    version="0.0.1",
    packages=find_packages(),
    install_requires=[
        "typer",
    ],
    entry_points={
        "console_scripts": [
            "cloudbrew = LCF.cli:app",
        ],
    },
    python_requires=">=3.9",
)
