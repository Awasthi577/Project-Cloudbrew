import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

@dataclass
class Violation:
    rule_id: str
    resource_name: str
    message: str
    severity: str = "ERROR"  # ERROR, WARNING

class PolicyValidator(ABC):
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abstractmethod
    def validate(self, spec: Dict[str, Any]) -> List[Violation]:
        pass

class RegionValidator(PolicyValidator):
    def validate(self, spec: Dict[str, Any]) -> List[Violation]:
        allowed = self.config.get("allowed_regions", ["us-east-1"])
        current = spec.get("region", "us-east-1")
        if current not in allowed:
            return [Violation(
                rule_id="REGION_CHECK",
                resource_name=spec.get("name", "unknown"),
                message=f"Region '{current}' is not in allowed list: {allowed}"
            )]
        return []

class InstanceTypeValidator(PolicyValidator):
    def validate(self, spec: Dict[str, Any]) -> List[Violation]:
        banned = self.config.get("banned_instance_types", [])
        current = spec.get("size", "").lower()
        if any(b in current for b in banned):
            return [Violation(
                rule_id="INSTANCE_TYPE_CHECK",
                resource_name=spec.get("name", "unknown"),
                message=f"Instance type '{current}' matches banned patterns: {banned}"
            )]
        return []

class TagValidator(PolicyValidator):
    def validate(self, spec: Dict[str, Any]) -> List[Violation]:
        required = self.config.get("required_tags", [])
        current_tags = spec.get("tags", {})
        missing = [tag for tag in required if tag not in current_tags]
        
        if missing:
            return [Violation(
                rule_id="TAG_CHECK",
                resource_name=spec.get("name", "unknown"),
                message=f"Missing required tags: {missing}"
            )]
        return []

class CostValidator(PolicyValidator):
    def validate(self, spec: Dict[str, Any]) -> List[Violation]:
        max_budget = self.config.get("max_monthly_cost", 1000)
        # In a real prod system, this would call Infracost or a pricing API
        # Here we use a heuristic based on size
        size = spec.get("size", "small")
        estimated_cost = 500 if "large" in size else (100 if "medium" in size else 20)
        
        if estimated_cost > max_budget:
             return [Violation(
                rule_id="COST_CHECK",
                resource_name=spec.get("name", "unknown"),
                message=f"Estimated cost ${estimated_cost} exceeds budget ${max_budget}"
            )]
        return []

class PolicyEngine:
    DEFAULT_CONFIG_PATH = "policies.json"

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or self.DEFAULT_CONFIG_PATH
        self.rules = self._load_config()
        self.validators: List[PolicyValidator] = [
            RegionValidator(self.rules),
            InstanceTypeValidator(self.rules),
            TagValidator(self.rules),
            CostValidator(self.rules)
        ]

    def _load_config(self) -> Dict[str, Any]:
        defaults = {
            "allowed_regions": ["us-east-1", "us-west-1", "eu-central-1"],
            "banned_instance_types": ["xlarge", "2xlarge", "metal"],
            "required_tags": ["Environment"],
            "max_monthly_cost": 200
        }
        
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    user_config = json.load(f)
                    defaults.update(user_config)
                    logger.info(f"Loaded policies from {self.config_path}")
            except Exception as e:
                logger.error(f"Failed to load policy file {self.config_path}: {e}")
                # We do not crash, we fallback to defaults for safety
        else:
            logger.info("No policy file found, using defaults.")
            
        return defaults

    def check(self, spec: Dict[str, Any]) -> List[Violation]:
        violations = []
        for validator in self.validators:
            try:
                violations.extend(validator.validate(spec))
            except Exception as e:
                logger.error(f"Validator {validator.__class__.__name__} crashed: {e}")
                # In strict mode, you might want to treat a crash as a violation
                violations.append(Violation(
                    rule_id="VALIDATOR_ERROR", 
                    resource_name=spec.get("name", "unknown"), 
                    message=f"Internal validator error: {e}"
                ))
        return violations