"""Schema-driven dynamic resource creation with interactive gap filling."""

import logging
import tempfile
from pathlib import Path
from LCF.cloud_adapters.opentofu_adapter import OpenTofuAdapter
from LCF.provisioning.validator import ProvisioningValidator

logger = logging.getLogger("cloudbrew.dynamic_resource_creator")


def _call_flexibly(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except TypeError as e:
        for n in range(len(args) - 1, -1, -1):
            try:
                return fn(*args[:n], **kwargs)
            except TypeError:
                continue
        raise

def _render_hcl_with_adapter(adapter, provider, resource, schema, user_inputs):
    if hasattr(adapter, "build_hcl_from_schema"):
        fn = adapter.build_hcl_from_schema
        try:
            return _call_flexibly(fn, provider, resource, schema, user_inputs)
        except TypeError:
            try:
                return _call_flexibly(fn, resource, provider, schema, user_inputs)
            except TypeError:
                try:
                    return _call_flexibly(fn, provider, resource, schema)
                except TypeError:
                    pass

    try:
        from LCF.cloud_adapters.opentofu_adapter import build_hcl_from_schema as top_fn
    except Exception:
        top_fn = None

    if top_fn:
        try:
            return _call_flexibly(top_fn, provider, resource, schema, user_inputs)
        except TypeError:
            try:
                return _call_flexibly(top_fn, provider, resource, schema)
            except TypeError:
                pass

    if hasattr(adapter, "_render_hcl_from_schema"):
        spec = {"provider": provider}
        if isinstance(user_inputs, dict):
            for k, v in user_inputs.items():
                spec[k] = v
        try:
            return _call_flexibly(adapter._render_hcl_from_schema, resource, resource, spec, schema)
        except TypeError:
            try:
                return _call_flexibly(adapter._render_hcl_from_schema, resource, resource, spec)
            except TypeError:
                pass

    raise RuntimeError("Could not find an HCL renderer on OpenTofuAdapter.")

def create_resource_with_validation(provider, resource, schema, args):
    adapter = OpenTofuAdapter()
    validator = ProvisioningValidator(tofu_bin=adapter.tofu_path or "tofu")
    user_inputs = {}

    loop = 0
    while True:
        loop += 1
        logger.debug("create_resource_with_validation loop=%s provider=%s resource=%s", loop, provider, resource)
        hcl = _render_hcl_with_adapter(adapter, provider, resource, schema, user_inputs)
        logger.debug("generated_hcl_snippet=%s", hcl[:500].replace("\n", "\\n"))

        provider_root = Path(f".cloudbrew_providers/{provider}")
        provider_root.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="cb_run_", dir=str(provider_root)))
        (tmp / "main.tf").write_text(hcl, encoding="utf-8")
        logger.debug("wrote main.tf to %s", (tmp / "main.tf"))

        report = validator.validate(schema=schema, values=user_inputs, rendered_hcl=hcl, workdir=tmp)
        retryable = [
            d
            for d in report.diagnostics
            if d.rule_id in {
                "SCHEMA_REQUIRED_ATTRIBUTE_MISSING",
                "SCHEMA_REQUIRED_BLOCK_MISSING",
                "SCHEMA_MIN_ITEMS_VIOLATION",
                "TOFU_REQUIRED_ARGUMENT_MISSING",
                "TOFU_REQUIRED_BLOCK_MISSING",
            }
        ]
        logger.debug(
            "validation success=%s retryable=%s",
            report.success,
            [d.rule_id + ":" + d.path for d in retryable],
        )

        if report.success:
            return {"success": True, "hcl": hcl, "diagnostics": []}

        if not retryable:
            return {
                "success": False,
                "hcl": hcl,
                "diagnostics": [diag.__dict__ for diag in report.diagnostics],
                "validation_raw": "\n".join(
                    f"[{step['command']}]\n{step.get('stdout', '')}\n{step.get('stderr', '')}" for step in report.command_results
                ).strip(),
            }

        missing_args = []
        missing_blocks = []
        for diag in retryable:
            if diag.rule_id in {"SCHEMA_REQUIRED_ATTRIBUTE_MISSING", "TOFU_REQUIRED_ARGUMENT_MISSING"}:
                if diag.path not in missing_args:
                    missing_args.append(diag.path)
            elif diag.rule_id in {"SCHEMA_REQUIRED_BLOCK_MISSING", "SCHEMA_MIN_ITEMS_VIOLATION", "TOFU_REQUIRED_BLOCK_MISSING"}:
                if diag.path not in missing_blocks:
                    missing_blocks.append(diag.path)

        if getattr(args, "yes", False):
            return {
                "success": False,
                "hcl": hcl,
                "diagnostics": [diag.__dict__ for diag in report.diagnostics],
                "error": (
                    "Missing required inputs in non-interactive mode (--yes). "
                    "Please provide all required flags explicitly."
                ),
                "missing_args": missing_args,
                "missing_blocks": missing_blocks,
            }

        logger.debug("interactive mode prompting for missing inputs")
        for a in missing_args:
            user_inputs[a] = input(f"Enter value for {a}: ")

        for blk in missing_blocks:
            user_inputs.setdefault(blk, {})
            nested = schema.get("block", {}).get("block_types", {}).get(blk, {}).get("block", {}).get("attributes", {})
            for aname, aspec in nested.items():
                if aspec.get("required") and aname not in user_inputs[blk]:
                    user_inputs[blk][aname] = input(f"{blk}.{aname}: ")
