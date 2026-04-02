from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple

import typer


@dataclass
class PromptContext:
    path: str
    required: bool
    advanced: bool
    schema: Dict[str, Any] = field(default_factory=dict)
    default: Any = None


class PromptAdapter(Protocol):
    def resolve_value(self, ctx: PromptContext) -> Tuple[bool, Any]:
        ...


class InteractiveTerminalPromptAdapter:
    def resolve_value(self, ctx: PromptContext) -> Tuple[bool, Any]:
        label = "required" if ctx.required else "optional"
        suffix = " [advanced]" if ctx.advanced else ""
        prompt_text = f"Enter {label} value for {ctx.path}{suffix}"
        if ctx.default is not None:
            return True, typer.prompt(prompt_text, default=str(ctx.default))
        return True, typer.prompt(prompt_text)


class NonInteractivePromptAdapter:
    def __init__(self, policy_defaults: Optional[Dict[str, Any]] = None, strict: bool = True) -> None:
        self.policy_defaults = policy_defaults or {}
        self.strict = strict

    def resolve_value(self, ctx: PromptContext) -> Tuple[bool, Any]:
        if ctx.path in self.policy_defaults:
            return True, self.policy_defaults[ctx.path]
        if ctx.default is not None:
            return True, ctx.default
        if ctx.required and self.strict:
            return False, None
        return True, None


@dataclass
class PromptResult:
    canonical_spec: Dict[str, Any]
    missing_required_paths: List[str]


class PromptEngine:
    def __init__(self, adapter: PromptAdapter) -> None:
        self.adapter = adapter

    def build_canonical_spec(
        self,
        schema: Dict[str, Any],
        current_spec: Dict[str, Any],
        prompt_advanced: bool = False,
    ) -> PromptResult:
        canonical = copy.deepcopy(current_spec or {})
        block = (schema or {}).get("block", {})

        missing_required: List[str] = []
        self._walk_block(
            block=block,
            node=canonical,
            base_path="",
            required_only=True,
            missing_required=missing_required,
        )
        if not missing_required and prompt_advanced:
            self._walk_block(
                block=block,
                node=canonical,
                base_path="",
                required_only=False,
                missing_required=[],
            )
        return PromptResult(canonical_spec=canonical, missing_required_paths=missing_required)

    def _walk_block(
        self,
        block: Dict[str, Any],
        node: Dict[str, Any],
        base_path: str,
        required_only: bool,
        missing_required: List[str],
    ) -> None:
        attributes = block.get("attributes", {})
        for attr_name, attr_schema in attributes.items():
            required = bool(attr_schema.get("required"))
            if required_only and not required:
                continue
            if not required_only and required:
                continue
            if attr_name in node:
                continue
            self._resolve_attribute(
                node=node,
                attr_name=attr_name,
                attr_schema=attr_schema,
                path=self._join_path(base_path, attr_name),
                required=required,
                advanced=not required_only,
                missing_required=missing_required,
            )

        block_types = block.get("block_types", {})
        for block_name, block_schema in block_types.items():
            min_items = int(block_schema.get("min_items", 0))
            required = min_items > 0

            block_path = self._join_path(base_path, block_name)
            if block_name not in node:
                if required_only and not required:
                    continue
                if not required_only and required:
                    continue
                inserted = self._initialize_block_value(
                    block_path=block_path,
                    block_name=block_name,
                    block_schema=block_schema,
                    required=required,
                    advanced=not required_only,
                    missing_required=missing_required,
                )
                if inserted is None:
                    continue
                node[block_name] = inserted

            for child in self._iter_nested_nodes(node.get(block_name), block_schema):
                self._walk_block(
                    block=block_schema.get("block", {}),
                    node=child,
                    base_path=block_path,
                    required_only=required_only,
                    missing_required=missing_required,
                )

    def _resolve_attribute(
        self,
        node: Dict[str, Any],
        attr_name: str,
        attr_schema: Dict[str, Any],
        path: str,
        required: bool,
        advanced: bool,
        missing_required: List[str],
    ) -> None:
        default = attr_schema.get("default")
        found, value = self.adapter.resolve_value(
            PromptContext(path=path, required=required, advanced=advanced, schema=attr_schema, default=default)
        )
        if not found and required:
            missing_required.append(path)
            return
        if found and value is not None:
            node[attr_name] = value

    def _initialize_block_value(
        self,
        block_path: str,
        block_name: str,
        block_schema: Dict[str, Any],
        required: bool,
        advanced: bool,
        missing_required: List[str],
    ) -> Any:
        default_structure = self._default_structure_for_block(block_schema)
        found, value = self.adapter.resolve_value(
            PromptContext(
                path=block_path,
                required=required,
                advanced=advanced,
                schema=block_schema,
                default=json.dumps(default_structure),
            )
        )
        if not found and required:
            missing_required.append(block_path)
            return None
        if value is None:
            return default_structure if required else None
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return default_structure if required else None
        return value

    @staticmethod
    def _iter_nested_nodes(value: Any, block_schema: Dict[str, Any]) -> List[Dict[str, Any]]:
        mode = block_schema.get("nesting_mode", "single")
        if mode in ("single", "group"):
            return [value] if isinstance(value, dict) else []
        if mode in ("list", "set"):
            if isinstance(value, list):
                return [v for v in value if isinstance(v, dict)]
            return []
        if mode == "map":
            if isinstance(value, dict):
                return [v for v in value.values() if isinstance(v, dict)]
            return []
        return []

    @staticmethod
    def _default_structure_for_block(block_schema: Dict[str, Any]) -> Any:
        mode = block_schema.get("nesting_mode", "single")
        if mode in ("single", "group"):
            return {}
        if mode in ("list", "set"):
            min_items = max(1, int(block_schema.get("min_items", 1)))
            return [{} for _ in range(min_items)]
        if mode == "map":
            return {"default": {}}
        return {}

    @staticmethod
    def _join_path(base_path: str, name: str) -> str:
        return name if not base_path else f"{base_path}.{name}"
