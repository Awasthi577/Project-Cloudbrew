#!/usr/bin/env python3

"""
Script to fix the CLI file by removing the legacy create command.
"""

import re

def fix_cli_file():
    """Remove the legacy create command from cli.py"""
    
    input_file = "LCF/cli.py.backup"
    output_file = "LCF/cli.py"
    
    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Find and remove the legacy create command section
    # The pattern starts with the comment and ends before the destroy-vm section
    pattern = r'# -------------------------------------------------------------\n#  Updated create \(Positional Args \+ Validation \+ Extras\) - DISABLED\n.*?# -------------------------------------------------------------\n#  destroy-vm \(OpenTofu \+ Pulumi \+ Offload\)'
    
    # Use re.DOTALL to match across multiple lines
    fixed_content = re.sub(pattern, '# -------------------------------------------------------------\n#  destroy-vm (OpenTofu + Pulumi + Offload)', content, flags=re.DOTALL)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(fixed_content)
    
    print("CLI file fixed successfully!")

if __name__ == "__main__":
    fix_cli_file()