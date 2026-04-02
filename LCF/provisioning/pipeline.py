from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer

from LCF.cloud_adapters.opentofu_adapter import OpenTofuAdapter
from LCF.resource_resolver import ResourceResolver


@dataclass
class CanonicalCreateRequest:
    name: str
    resource_type: str
    provider_hint: str = "auto"
    provider_version: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    plan_only: bool = True
    non_interactive: bool = False


class ProvisioningPipeline:
    """
    Canonical create pipeline:
    parse -> resolve -> schema -> gap -> collect -> typed config -> render -> tofu exec.
    """

    def __init__(self) -> None:
        self.resolver = ResourceResolver()
        self.adapter = OpenTofuAdapter()
        self.tofu_bin = self.adapter.tofu_path or "tofu"

    def execute(self, raw_request: Dict[str, Any]) -> Dict[str, Any]:
        req = self._parse_canonical_request(raw_request)
        resolved = self._resolve_provider_and_type(req)
        schema_info = self._load_provider_schema(resolved)
        gap = self._run_gap_analysis(schema_info["resource_schema"], resolved["attributes"])
        final_values = self._collect_missing_values(gap, resolved["attributes"], req.non_interactive)
        typed = self._build_typed_config(final_values, schema_info["resource_schema"])
        rendered = self._render_from_typed_object(req.name, resolved["resource_type"], resolved["provider"], typed, schema_info["resource_schema"])
        tofu = self._run_tofu_stages(req.name, rendered["hcl"], run_apply=not req.plan_only)

        return {
            "success": tofu["success"],
            "request": req.__dict__,
            "resolved": {k: v for k, v in resolved.items() if k != "attributes"},
            "provider_schema": schema_info["provider_schema_key"],
            "provider_version": schema_info["provider_version"],
            "gap_analysis": gap,
            "typed_config": typed,
            "rendered": rendered,
            "tofu": tofu,
        }

    def _parse_canonical_request(self, raw: Dict[str, Any]) -> CanonicalCreateRequest:
        attrs = dict(raw.get("attributes", {}))
        for control_key in ("name", "resource_type", "provider", "provider_hint", "provider_version", "plan_only", "non_interactive"):
            attrs.pop(control_key, None)
        return CanonicalCreateRequest(
            name=raw["name"],
            resource_type=raw["resource_type"],
            provider_hint=raw.get("provider") or raw.get("provider_hint") or "auto",
            provider_version=raw.get("provider_version"),
            attributes={**attrs, **raw.get("attributes", {})},
            plan_only=bool(raw.get("plan_only", True)),
            non_interactive=bool(raw.get("non_interactive", False)),
        )

    def _resolve_provider_and_type(self, req: CanonicalCreateRequest) -> Dict[str, Any]:
        resolved = self.resolver.resolve(req.resource_type, req.provider_hint)
        if isinstance(resolved, dict) and resolved.get("_resolved"):
            resource_type = resolved["_resolved"]
            provider = resolved.get("_provider") or req.provider_hint
            defaults = resolved.get("_defaults", {})
        else:
            resource_type = req.resource_type
            provider = req.provider_hint
            defaults = {}

        if provider in (None, "auto"):
            provider = "opentofu"

        merged_attributes = dict(defaults)
        merged_attributes.update(req.attributes)
        return {
            "resource_type": resource_type,
            "provider": provider,
            "provider_version": req.provider_version,
            "attributes": merged_attributes,
        }

    def _load_provider_schema(self, resolved: Dict[str, Any]) -> Dict[str, Any]:
        provider = (resolved["provider"] or "opentofu").lower()
        raw = self.resolver._query_opentofu_schema(provider=provider) or {}
        provider_schemas = raw.get("provider_schemas", {})

        resource_schema = {}
        provider_schema_key = None
        for key, pdata in provider_schemas.items():
            if resolved["resource_type"] in (pdata.get("resource_schemas") or {}):
                provider_schema_key = key
                resource_schema = pdata["resource_schemas"][resolved["resource_type"]]
                break

        if not resource_schema:
            resource_schema = self.adapter.schema_mgr.get(resolved["resource_type"]) or {}

        return {
            "provider_schema_key": provider_schema_key or provider,
            "provider_version": resolved.get("provider_version") or "latest",
            "resource_schema": resource_schema,
        }

    def _run_gap_analysis(self, schema: Dict[str, Any], values: Dict[str, Any]) -> Dict[str, Any]:
        block = schema.get("block", {})
        attrs = block.get("attributes", {})
        blocks = block.get("block_types", {})
        missing_required_fields = [k for k, v in attrs.items() if v.get("required") and k not in values]
        missing_required_blocks = [k for k, v in blocks.items() if int(v.get("min_items", 0)) > 0 and k not in values]
        return {
            "missing_required_fields": missing_required_fields,
            "missing_required_blocks": missing_required_blocks,
            "ok": not missing_required_fields and not missing_required_blocks,
        }

    def _collect_missing_values(self, gap: Dict[str, Any], values: Dict[str, Any], non_interactive: bool) -> Dict[str, Any]:
        updated = dict(values)
        required = gap["missing_required_fields"] + gap["missing_required_blocks"]
        if not required:
            return updated
        if non_interactive:
            raise typer.BadParameter(f"Missing required fields in non-interactive mode: {', '.join(required)}")
        for field in gap["missing_required_fields"]:
            updated[field] = typer.prompt(f"Enter value for {field}")
        for block in gap["missing_required_blocks"]:
            raw = typer.prompt(f"Enter JSON object for required block {block}", default="{}")
            updated[block] = json.loads(raw)
        return updated

    def _build_typed_config(self, values: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        typed = {}
        attrs = schema.get("block", {}).get("attributes", {})
        for k, v in values.items():
            expected = attrs.get(k, {}).get("type")
            typed[k] = self._coerce_value(v, expected)
        return typed

    def _coerce_value(self, value: Any, expected: Any) -> Any:
        if not isinstance(value, str):
            return value
        if expected == "number":
            try:
                return float(value) if "." in value else int(value)
            except ValueError:
                return value
        if expected == "bool":
            return value.lower() == "true"
        return value

    def _render_from_typed_object(self, logical_name: str, resource_type: str, provider: str, typed: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        hcl = self.adapter._render_hcl_from_schema(resource_type, logical_name, {"provider": provider, **typed}, schema)
        return {"hcl": hcl, "json": typed}

    def _run_tofu_stages(self, logical_name: str, hcl: str, run_apply: bool) -> Dict[str, Any]:
        workdir = Path(self.adapter._workdir_for(logical_name))
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "main.tf").write_text(hcl, encoding="utf-8")

        commands: List[List[str]] = [
            [self.tofu_bin, "init", "-no-color"],
            [self.tofu_bin, "validate", "-no-color"],
            [self.tofu_bin, "plan", "-no-color"],
        ]
        if run_apply:
            commands.append([self.tofu_bin, "apply", "-auto-approve", "-no-color"])

        results = []
        env = os.environ.copy()
        env["TF_IN_AUTOMATION"] = "1"
        for cmd in commands:
            proc = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True, env=env)
            results.append(
                {
                    "command": " ".join(cmd),
                    "returncode": proc.returncode,
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                }
            )
            if proc.returncode != 0:
                return {"success": False, "steps": results, "workdir": str(workdir)}
        return {"success": True, "steps": results, "workdir": str(workdir)}
