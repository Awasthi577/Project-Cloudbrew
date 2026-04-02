from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


class HCLIRRenderer:
    """
    Deterministic HCL renderer for typed IR dictionaries using provider schema.

    Supported structures:
      - scalar attributes
      - maps/objects
      - list/set values
      - nested blocks driven by schema block_types + nesting_mode
    """

    INDENT = "  "

    def render_resource(
        self,
        resource_type: str,
        logical_name: str,
        ir: Dict[str, Any],
        schema: Optional[Dict[str, Any]] = None,
    ) -> str:
        safe_name = self._sanitize_name(logical_name)
        block_schema = (schema or {}).get("block", {}) if schema else {}
        attrs_schema = block_schema.get("attributes", {}) if block_schema else {}
        block_types = block_schema.get("block_types", {}) if block_schema else {}

        lines: List[str] = [f'resource "{resource_type}" "{safe_name}" {{']

        attr_names = sorted(k for k in ir.keys() if k in attrs_schema)
        block_names = sorted(k for k in ir.keys() if k in block_types)
        extra_names = sorted(
            k
            for k in ir.keys()
            if k not in attrs_schema and k not in block_types and not k.startswith("_") and k != "provider"
        )

        for name in attr_names:
            lines.append(self._render_attribute(name, ir[name], depth=1))

        for name in block_names:
            lines.extend(self._render_block_type(name, ir[name], block_types[name], depth=1))

        for name in extra_names:
            # Best-effort: unknown keys are rendered as normal attributes.
            lines.append(self._render_attribute(name, ir[name], depth=1))

        lines.append("}")
        return "\n".join(lines)

    def _render_block_type(self, name: str, value: Any, block_schema: Dict[str, Any], depth: int) -> List[str]:
        mode = (block_schema or {}).get("nesting_mode") or "single"
        nested_block = (block_schema or {}).get("block", {})

        if mode in ("list", "set"):
            items = value if isinstance(value, list) else [value]
            out: List[str] = []
            for item in items:
                out.extend(self._render_single_block(name, item, nested_block, depth))
            return out

        # single/group/map are emitted as one block.
        return self._render_single_block(name, value, nested_block, depth)

    def _render_single_block(self, block_name: str, value: Any, nested_schema: Dict[str, Any], depth: int) -> List[str]:
        indent = self.INDENT * depth
        lines: List[str] = [f"{indent}{block_name} {{"]

        if isinstance(value, dict):
            attrs = nested_schema.get("attributes", {}) if nested_schema else {}
            nested_blocks = nested_schema.get("block_types", {}) if nested_schema else {}

            attr_names = sorted(k for k in value.keys() if k in attrs)
            block_names = sorted(k for k in value.keys() if k in nested_blocks)
            extra_names = sorted(k for k in value.keys() if k not in attrs and k not in nested_blocks)

            for name in attr_names:
                lines.append(self._render_attribute(name, value[name], depth + 1))
            for name in block_names:
                lines.extend(self._render_block_type(name, value[name], nested_blocks[name], depth + 1))
            for name in extra_names:
                lines.append(self._render_attribute(name, value[name], depth + 1))

        lines.append(f"{indent}}}")
        return lines

    def _render_attribute(self, key: str, value: Any, depth: int) -> str:
        indent = self.INDENT * depth
        return f"{indent}{key} = {self._render_value(value, depth)}"

    def _render_value(self, value: Any, depth: int) -> str:
        if isinstance(value, dict):
            return self._render_object(value, depth)
        if isinstance(value, list):
            return self._render_list(value, depth)
        return json.dumps(value)

    def _render_object(self, value: Dict[str, Any], depth: int) -> str:
        indent = self.INDENT * depth
        child_indent = self.INDENT * (depth + 1)
        items = sorted(value.items(), key=lambda kv: kv[0])
        if not items:
            return "{}"

        lines = ["{"]
        for k, v in items:
            rendered_key = self._render_object_key(k)
            rendered_value = self._render_value(v, depth + 1)
            lines.append(f"{child_indent}{rendered_key} = {rendered_value}")
        lines.append(f"{indent}}}")
        return "\n".join(lines)

    def _render_list(self, value: List[Any], depth: int) -> str:
        if not value:
            return "[]"
        return json.dumps(value)

    @staticmethod
    def _render_object_key(key: str) -> str:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            return key
        return json.dumps(key)

    @staticmethod
    def _sanitize_name(logical_name: str) -> str:
        clean_name = re.sub(r"[^A-Za-z0-9_]", "_", logical_name.replace(" ", "_"))
        if clean_name and clean_name[0].isdigit():
            clean_name = f"res_{clean_name}"
        return clean_name
