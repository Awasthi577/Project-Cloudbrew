from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ValidationDiagnostic:
    rule_id: str
    tier: str
    path: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationReport:
    success: bool
    diagnostics: List[ValidationDiagnostic] = field(default_factory=list)
    command_results: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def has_schema_errors(self) -> bool:
        return any(d.tier == "tier1" for d in self.diagnostics)

    @property
    def missing_required_paths(self) -> List[str]:
        paths: List[str] = []
        for diag in self.diagnostics:
            if diag.rule_id in {
                "SCHEMA_REQUIRED_ATTRIBUTE_MISSING",
                "SCHEMA_REQUIRED_BLOCK_MISSING",
                "SCHEMA_MIN_ITEMS_VIOLATION",
                "TOFU_REQUIRED_ARGUMENT_MISSING",
                "TOFU_REQUIRED_BLOCK_MISSING",
            }:
                if diag.path and diag.path not in paths:
                    paths.append(diag.path)
        return paths


class ProvisioningValidator:
    """
    Two-tier validator:
      - Tier 1: schema-native structural checks.
      - Tier 2: OpenTofu semantic checks (`validate` + `plan`) on rendered HCL.
    """

    def __init__(self, tofu_bin: str = "tofu") -> None:
        self.tofu_bin = tofu_bin or "tofu"

    def validate(
        self,
        schema: Dict[str, Any],
        values: Dict[str, Any],
        *,
        rendered_hcl: Optional[str] = None,
        workdir: Optional[Path] = None,
    ) -> ValidationReport:
        diagnostics = self._validate_tier1(schema or {}, values or {}, base_path="")

        command_results: List[Dict[str, Any]] = []
        if rendered_hcl is not None and workdir is not None:
            tier2_diagnostics, command_results = self._validate_tier2(rendered_hcl, Path(workdir))
            diagnostics.extend(tier2_diagnostics)

        return ValidationReport(success=not diagnostics, diagnostics=diagnostics, command_results=command_results)

    def _validate_tier1(self, schema: Dict[str, Any], node: Any, base_path: str) -> List[ValidationDiagnostic]:
        block = schema.get("block", {}) if isinstance(schema, dict) else {}
        diagnostics: List[ValidationDiagnostic] = []

        if not isinstance(node, dict):
            diagnostics.append(
                ValidationDiagnostic(
                    rule_id="SCHEMA_OBJECT_EXPECTED",
                    tier="tier1",
                    path=base_path or "$",
                    message="Expected an object for schema block.",
                    details={"expected_type": "object", "actual_type": type(node).__name__},
                )
            )
            return diagnostics

        for attr_name, attr_schema in (block.get("attributes") or {}).items():
            path = self._join_path(base_path, attr_name)
            required = bool(attr_schema.get("required"))
            if required and attr_name not in node:
                diagnostics.append(
                    ValidationDiagnostic(
                        rule_id="SCHEMA_REQUIRED_ATTRIBUTE_MISSING",
                        tier="tier1",
                        path=path,
                        message=f"Missing required attribute '{attr_name}'.",
                    )
                )
                continue
            if attr_name in node:
                expected_type = attr_schema.get("type")
                if not self._matches_type(node[attr_name], expected_type):
                    diagnostics.append(
                        ValidationDiagnostic(
                            rule_id="SCHEMA_ATTRIBUTE_TYPE_MISMATCH",
                            tier="tier1",
                            path=path,
                            message=f"Attribute '{attr_name}' has an invalid type.",
                            details={"expected": expected_type, "actual": type(node[attr_name]).__name__},
                        )
                    )

        for block_name, block_schema in (block.get("block_types") or {}).items():
            path = self._join_path(base_path, block_name)
            min_items = int(block_schema.get("min_items", 0) or 0)
            max_items = block_schema.get("max_items")
            mode = block_schema.get("nesting_mode", "single")
            present = node.get(block_name)

            if present is None:
                if min_items > 0:
                    diagnostics.append(
                        ValidationDiagnostic(
                            rule_id="SCHEMA_REQUIRED_BLOCK_MISSING",
                            tier="tier1",
                            path=path,
                            message=f"Missing required block '{block_name}'.",
                            details={"min_items": min_items, "nesting_mode": mode},
                        )
                    )
                continue

            nested_nodes: List[Any] = []
            if mode in ("single", "group"):
                if not isinstance(present, dict):
                    diagnostics.append(
                        ValidationDiagnostic(
                            rule_id="SCHEMA_BLOCK_TYPE_MISMATCH",
                            tier="tier1",
                            path=path,
                            message=f"Block '{block_name}' must be an object.",
                            details={"nesting_mode": mode, "actual": type(present).__name__},
                        )
                    )
                    continue
                nested_nodes = [present]
            elif mode in ("list", "set"):
                if not isinstance(present, list):
                    diagnostics.append(
                        ValidationDiagnostic(
                            rule_id="SCHEMA_BLOCK_TYPE_MISMATCH",
                            tier="tier1",
                            path=path,
                            message=f"Block '{block_name}' must be a list.",
                            details={"nesting_mode": mode, "actual": type(present).__name__},
                        )
                    )
                    continue
                if len(present) < min_items:
                    diagnostics.append(
                        ValidationDiagnostic(
                            rule_id="SCHEMA_MIN_ITEMS_VIOLATION",
                            tier="tier1",
                            path=path,
                            message=f"Block '{block_name}' requires at least {min_items} item(s).",
                            details={"min_items": min_items, "actual_items": len(present)},
                        )
                    )
                if max_items is not None and len(present) > int(max_items):
                    diagnostics.append(
                        ValidationDiagnostic(
                            rule_id="SCHEMA_MAX_ITEMS_VIOLATION",
                            tier="tier1",
                            path=path,
                            message=f"Block '{block_name}' allows at most {max_items} item(s).",
                            details={"max_items": int(max_items), "actual_items": len(present)},
                        )
                    )
                nested_nodes = present
            elif mode == "map":
                if not isinstance(present, dict):
                    diagnostics.append(
                        ValidationDiagnostic(
                            rule_id="SCHEMA_BLOCK_TYPE_MISMATCH",
                            tier="tier1",
                            path=path,
                            message=f"Block '{block_name}' must be a map/object.",
                            details={"nesting_mode": mode, "actual": type(present).__name__},
                        )
                    )
                    continue
                if min_items > 0 and len(present) < min_items:
                    diagnostics.append(
                        ValidationDiagnostic(
                            rule_id="SCHEMA_MIN_ITEMS_VIOLATION",
                            tier="tier1",
                            path=path,
                            message=f"Block '{block_name}' requires at least {min_items} map entrie(s).",
                            details={"min_items": min_items, "actual_items": len(present)},
                        )
                    )
                if max_items is not None and len(present) > int(max_items):
                    diagnostics.append(
                        ValidationDiagnostic(
                            rule_id="SCHEMA_MAX_ITEMS_VIOLATION",
                            tier="tier1",
                            path=path,
                            message=f"Block '{block_name}' allows at most {max_items} map entrie(s).",
                            details={"max_items": int(max_items), "actual_items": len(present)},
                        )
                    )
                nested_nodes = list(present.values())

            child_schema = {"block": block_schema.get("block", {})}
            for idx, child in enumerate(nested_nodes):
                child_path = f"{path}[{idx}]" if mode in ("list", "set") else path
                diagnostics.extend(self._validate_tier1(child_schema, child, child_path))

        return diagnostics

    def _validate_tier2(self, rendered_hcl: str, workdir: Path) -> tuple[List[ValidationDiagnostic], List[Dict[str, Any]]]:
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "main.tf").write_text(rendered_hcl, encoding="utf-8")

        env = os.environ.copy()
        env["TF_IN_AUTOMATION"] = "1"
        commands = [
            [self.tofu_bin, "init", "-no-color"],
            [self.tofu_bin, "validate", "-no-color"],
            [self.tofu_bin, "plan", "-no-color"],
        ]

        diagnostics: List[ValidationDiagnostic] = []
        command_results: List[Dict[str, Any]] = []
        for cmd in commands:
            proc = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True, env=env)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            output = f"{stdout}\n{stderr}".strip()
            command_results.append({
                "command": " ".join(cmd),
                "returncode": proc.returncode,
                "stdout": stdout,
                "stderr": stderr,
            })
            if proc.returncode != 0:
                diagnostics.extend(self._extract_tofu_diagnostics(output, cmd[1]))
                if not diagnostics or all(d.tier != "tier2" for d in diagnostics):
                    diagnostics.append(
                        ValidationDiagnostic(
                            rule_id="TOFU_COMMAND_FAILED",
                            tier="tier2",
                            path="$",
                            message=f"OpenTofu command failed: {' '.join(cmd)}",
                            details={"returncode": proc.returncode, "output": output},
                        )
                    )
                break

        return diagnostics, command_results

    def _extract_tofu_diagnostics(self, output: str, stage: str) -> List[ValidationDiagnostic]:
        diagnostics: List[ValidationDiagnostic] = []

        for arg in re.findall(r'The argument "([^"]+)" is required', output):
            diagnostics.append(
                ValidationDiagnostic(
                    rule_id="TOFU_REQUIRED_ARGUMENT_MISSING",
                    tier="tier2",
                    path=arg,
                    message=f"OpenTofu reports missing required argument '{arg}'.",
                    details={"stage": stage},
                )
            )

        for block in re.findall(r'A block "([^"]+)" is required', output):
            diagnostics.append(
                ValidationDiagnostic(
                    rule_id="TOFU_REQUIRED_BLOCK_MISSING",
                    tier="tier2",
                    path=block,
                    message=f"OpenTofu reports missing required block '{block}'.",
                    details={"stage": stage},
                )
            )

        for ref in re.findall(r"Reference to undeclared [^\n]*", output):
            diagnostics.append(
                ValidationDiagnostic(
                    rule_id="TOFU_UNDECLARED_REFERENCE",
                    tier="tier2",
                    path="$",
                    message=ref,
                    details={"stage": stage},
                )
            )

        return diagnostics

    @staticmethod
    def _join_path(base_path: str, name: str) -> str:
        return name if not base_path else f"{base_path}.{name}"

    @staticmethod
    def _matches_type(value: Any, expected_type: Any) -> bool:
        if expected_type is None:
            return True
        if isinstance(expected_type, str):
            mapping = {
                "string": str,
                "number": (int, float),
                "bool": bool,
                "list": list,
                "set": (list, set, tuple),
                "map": dict,
                "object": dict,
                "any": object,
            }
            py_type = mapping.get(expected_type)
            if py_type is None:
                return True
            if expected_type == "number" and isinstance(value, bool):
                return False
            return isinstance(value, py_type)

        if isinstance(expected_type, (list, tuple)) and expected_type:
            kind = expected_type[0]
            if kind in ("list", "set", "tuple"):
                return isinstance(value, list)
            if kind == "map":
                return isinstance(value, dict)
            if kind == "object":
                return isinstance(value, dict)
        return True
