# tofu_validator.py

import subprocess
import re
from pathlib import Path

class TofuValidationResult:
    def __init__(self, success, missing_args=None, missing_blocks=None, raw_output=""):
        self.success = success
        self.missing_args = missing_args or []
        self.missing_blocks = missing_blocks or []
        self.raw_output = raw_output

def run_tofu_validate(workdir: Path) -> TofuValidationResult:
    """Run `tofu validate` and extract missing arguments/blocks."""
    proc = subprocess.run(
        ["tofu", "validate", "-no-color"],
        cwd=str(workdir),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    output = proc.stdout + "\n" + proc.stderr
    
    if proc.returncode == 0:
        return TofuValidationResult(True, raw_output=output)

    # Extract missing ARGUMENT errors
    missing_args = re.findall(
        r'The argument "([^"]+)" is required', output
    )

    # Extract missing BLOCK errors
    missing_blocks = re.findall(
        r'A block "([^"]+)" is required', output
    )

    return TofuValidationResult(
        False,
        missing_args=missing_args,
        missing_blocks=missing_blocks,
        raw_output=output
    )
