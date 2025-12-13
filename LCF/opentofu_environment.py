#!/usr/bin/env python3
"""
Persistent OpenTofu Environment for Fast Validation
This module maintains a persistent OpenTofu environment to avoid
repeated initialization overhead.
"""

import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional
import json


class OpenTofuEnvironment:
    """
    A persistent OpenTofu environment that can be reused across multiple validations.
    This eliminates the overhead of repeated 'tofu init' calls.
    """
    
    _instance = None
    _lock = threading.Lock()
    _initialized = False
    
    def __new__(cls):
        """Singleton pattern - ensure only one instance exists"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(OpenTofuEnvironment, cls).__new__(cls)
                    cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        """Initialize the persistent OpenTofu environment"""
        if self._initialized:
            return
        
        self._initialized = True
        self.workdir = self._create_persistent_workdir()
        self._ensure_opentofu_initialized()
        
    def _create_persistent_workdir(self) -> Path:
        """Create a persistent working directory for OpenTofu"""
        workdir = Path(".cloudbrew_tofu_env")
        workdir.mkdir(exist_ok=True)
        return workdir
    
    def _ensure_opentofu_initialized(self):
        """Ensure OpenTofu is initialized in the working directory"""
        # Check if already initialized
        if (self.workdir / ".terraform").exists():
            return
        
        # Create minimal main.tf to allow init
        main_tf = self.workdir / "main.tf"
        if not main_tf.exists():
            main_tf.write_text("# CloudBrew OpenTofu Environment\n")
        
        # Run tofu init with optimizations
        try:
            subprocess.run(
                [
                    'tofu', 'init',
                    '-backend=false',  # Skip backend configuration
                    '-upgrade=false',  # Don't upgrade providers
                    '-input=false'    # Non-interactive
                ],
                cwd=self.workdir,
                capture_output=True,
                timeout=120
            )
        except subprocess.TimeoutExpired:
            pass  # Continue even if init times out
        except Exception:
            pass  # Continue even if init fails
    
    def validate_config(self, config: Dict) -> List[str]:
        """
        Validate a configuration using the persistent OpenTofu environment.
        This is much faster than creating a new environment each time.
        """
        errors = []
        
        try:
            # Write the configuration to a temporary file
            temp_config_file = self.workdir / f"temp_{int(time.time())}.tf"
            hcl_content = self._dict_to_hcl(config)
            temp_config_file.write_text(hcl_content)
            
            # Run validation
            result = subprocess.run(
                ['tofu', 'validate', str(temp_config_file)],
                cwd=self.workdir,
                capture_output=True,
                text=True,
                timeout=30  # Much shorter timeout since environment is pre-initialized
            )
            
            # Clean up temp file
            temp_config_file.unlink()
            
            if result.returncode != 0:
                errors = self._parse_opentofu_errors(result.stderr)
            
        except subprocess.TimeoutExpired:
            errors.append("OpenTofu validation timed out")
        except Exception as e:
            errors.append(f"Validation exception: {str(e)}")
        
        return errors
    
    def _parse_opentofu_errors(self, stderr: str) -> List[str]:
        """Parse OpenTofu error output into clean error messages"""
        errors = []
        
        for line in stderr.split('\n'):
            line = line.strip()
            
            # Skip empty lines and ANSI color codes
            if not line or line.startswith('Γ') or line.startswith('\x1b'):
                continue
            
            # Extract meaningful error messages
            if 'Error:' in line:
                error = line.split('Error:', 1)[1].strip()
                if error:
                    errors.append(error)
            elif 'missing required' in line:
                errors.append(line)
            elif 'invalid' in line.lower():
                errors.append(line)
        
        return errors if errors else ["OpenTofu validation failed"]
    
    def _dict_to_hcl(self, config: Dict) -> str:
        """Convert configuration dict to HCL"""
        from LCF.intelligent_builder import IntelligentBuilder
        builder = IntelligentBuilder()
        return builder._config_to_hcl(config)
    
    def cleanup(self):
        """Clean up temporary files"""
        for temp_file in self.workdir.glob("temp_*.tf"):
            try:
                temp_file.unlink()
            except Exception:
                pass


class OpenTofuEnvironmentPool:
    """
    Pool of OpenTofu environments for parallel validation.
    This allows multiple validations to happen concurrently.
    """
    
    def __init__(self, size: int = 4):
        self.size = size
        self.environments = [OpenTofuEnvironment() for _ in range(size)]
        self.current = 0
        self.lock = threading.Lock()
    
    def get_environment(self) -> OpenTofuEnvironment:
        """Get an environment from the pool"""
        with self.lock:
            env = self.environments[self.current]
            self.current = (self.current + 1) % self.size
            return env


# Global environment pool
environment_pool = OpenTofuEnvironmentPool(size=4)