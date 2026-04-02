from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer

from LCF.cloud_adapters.opentofu_adapter import OpenTofuAdapter
from LCF.provisioning.prompt_engine import (
    InteractiveTerminalPromptAdapter,
    NonInteractivePromptAdapter,
    PromptEngine,
)
from LCF.resource_resolver import ResourceResolver
from LCF.provisioning.run_metadata import RunMetadataStore


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

    def __init__(self, run_store_path: str = ".cloudbrew/runs.sqlite") -> None:
        self.resolver = ResourceResolver()
        self.adapter = OpenTofuAdapter()
        self.tofu_bin = self.adapter.tofu_path or "tofu"
        self.run_store = RunMetadataStore(run_store_path)

    def execute(self, raw_request: Dict[str, Any]) -> Dict[str, Any]:
        req = self._parse_canonical_request(raw_request)
        resolved = self._resolve_provider_and_type(req)
        schema_info = self._load_provider_schema(resolved)
        prompt_advanced = bool(raw_request.get("advanced"))
        pre_prompt_values = dict(resolved["attributes"])
        final_values, gap = self._collect_missing_values(
            schema=schema_info["resource_schema"],
            values=resolved["attributes"],
            non_interactive=req.non_interactive,
            prompt_advanced=prompt_advanced,
        )
        typed = self._build_typed_config(final_values, schema_info["resource_schema"])
        rendered = self._render_from_typed_object(req.name, resolved["resource_type"], resolved["provider"], typed, schema_info["resource_schema"])
        tofu = self._run_tofu_stages(req.name, rendered["hcl"], run_apply=not req.plan_only)
        schema_hash = self._stable_hash(schema_info["resource_schema"])
        prompted_fields = self._derive_prompted_fields(pre_prompt_values, final_values)
        hcl_artifact_path = str(Path(tofu["workdir"]) / "main.tf")

        response = {
            "success": tofu["success"],
            "request": req.__dict__,
            "resolved": {k: v for k, v in resolved.items() if k != "attributes"},
            "provider_schema": schema_info["provider_schema_key"],
            "provider_version": schema_info["provider_version"],
            "schema_hash": schema_hash,
            "gap_analysis": gap,
            "prompted_fields": prompted_fields,
            "final_spec": final_values,
            "typed_config": typed,
            "rendered": rendered,
            "hcl_artifact_path": hcl_artifact_path,
            "tofu": tofu,
        }
        run_id = self.run_store.save(
            {
                "resolved_identity": response["resolved"],
                "schema": {
                    "provider_schema": response["provider_schema"],
                    "provider_version": response["provider_version"],
                    "schema_hash": schema_hash,
                },
                "prompted_fields": prompted_fields,
                "final_spec": final_values,
                "hcl_artifact_path": hcl_artifact_path,
                "execution_timeline": tofu.get("steps", []),
                "request": req.__dict__,
                "success": response["success"],
            },
            run_id=raw_request.get("run_id"),
        )
        response["run_id"] = run_id
        return response

    def replay(self, run_id: str) -> Dict[str, Any]:
        record = self.run_store.get(run_id)
        if not record:
            raise typer.BadParameter(f"Unknown run_id: {run_id}")
        payload = record.payload
        identity = payload.get("resolved_identity", {})
        request = payload.get("request", {})
        replay_req = {
            "name": identity.get("identity", {}).get("logical_name") or request.get("name"),
            "resource_type": identity.get("resource_type") or request.get("resource_type"),
            "provider": identity.get("provider") or request.get("provider_hint") or "opentofu",
            "attributes": payload.get("final_spec", {}),
            "plan_only": bool(request.get("plan_only", True)),
            "non_interactive": True,
            "run_id": run_id,
        }
        return self.execute(replay_req)

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        record = self.run_store.get(run_id)
        if not record:
            return None
        return {"run_id": record.run_id, "created_at": record.created_at, **record.payload}

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
        resolved = self.resolver.canonicalize_identity(
            resource=req.resource_type,
            provider_hint=req.provider_hint,
            logical_name=req.name,
        )
        if isinstance(resolved, dict) and resolved.get("mode") == "provider_native_type_unmapped":
            raise typer.BadParameter(resolved.get("message", "Unable to resolve resource type"))
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
            "identity": resolved.get("_identity", {"provider": provider, "resource_type": resource_type, "logical_name": req.name}),
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
            resource_schema = self.adapter.schema_mgr.get_for_identity(resolved.get("identity", {})) or {}

        return {
            "provider_schema_key": provider_schema_key or provider,
            "provider_version": resolved.get("provider_version") or "latest",
            "resource_schema": resource_schema,
        }

    def _collect_missing_values(
        self,
        schema: Dict[str, Any],
        values: Dict[str, Any],
        non_interactive: bool,
        prompt_advanced: bool,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        adapter = NonInteractivePromptAdapter(strict=True) if non_interactive else InteractiveTerminalPromptAdapter()
        result = PromptEngine(adapter).build_canonical_spec(
            schema=schema,
            current_spec=values,
            prompt_advanced=prompt_advanced,
        )
        missing = result.missing_required_paths
        gap = {
            "missing_required_fields": missing,
            "missing_required_blocks": [],
            "ok": not missing,
        }
        if missing and non_interactive:
            raise typer.BadParameter(f"Missing required fields in non-interactive mode: {', '.join(missing)}")
        return result.canonical_spec, gap

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
        ir = {"provider": provider, **typed}
        identity = {"provider": provider, "resource_type": resource_type, "logical_name": logical_name}
        hcl = self.adapter.ir_renderer.render_resource(
            resource_type=resource_type,
            logical_name=logical_name,
            ir=self.adapter._alias_and_defaults({**ir, "_identity": identity}, resource_type, provider),
            schema=schema,
            identity=identity,
        )
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
            started = time.time()
            proc = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True, env=env)
            finished = time.time()
            results.append(
                {
                    "command": " ".join(cmd),
                    "returncode": proc.returncode,
                    "started_at": started,
                    "finished_at": finished,
                    "duration_seconds": round(finished - started, 6),
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                }
            )
            if proc.returncode != 0:
                return {"success": False, "steps": results, "workdir": str(workdir)}
        return {"success": True, "steps": results, "workdir": str(workdir)}

    def _stable_hash(self, value: Any) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()

    def _derive_prompted_fields(self, before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
        return sorted(self._flatten_paths(after) - self._flatten_paths(before))

    def _flatten_paths(self, value: Any, prefix: str = "") -> set[str]:
        paths: set[str] = set()
        if isinstance(value, dict):
            for key, val in value.items():
                next_prefix = f"{prefix}.{key}" if prefix else key
                paths.add(next_prefix)
                paths.update(self._flatten_paths(val, next_prefix))
        elif isinstance(value, list):
            for item in value:
                paths.update(self._flatten_paths(item, prefix))
        return paths
