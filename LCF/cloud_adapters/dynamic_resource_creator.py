# LCF/cloud_adapters/dynamic_resource_creator.py
from pathlib import Path
import inspect
from LCF.cloud_adapters.opentofu_adapter import OpenTofuAdapter
from LCF.provisioning.validator import ProvisioningValidator

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

    print("[DBG] enter create_resource_with_validation", flush=True)
    loop = 0
    while True:
        loop += 1
        print(f"[DBG] loop #{loop} building HCL (provider={provider} resource={resource})", flush=True)
        hcl = _render_hcl_with_adapter(adapter, provider, resource, schema, user_inputs)
        snippet = hcl[:800].replace("\n", "\\n")
        print(f"[DBG] generated HCL snippet: {snippet}", flush=True)

        tmp = Path(f".cloudbrew_providers/{provider}/_cb_tmp")
        tmp.mkdir(parents=True, exist_ok=True)
        (tmp / "main.tf").write_text(hcl, encoding="utf-8")
        print(f"[DBG] wrote main.tf to {tmp}\\main.tf", flush=True)

        print("[DBG] running two-tier validation (schema + tofu validate/plan)...", flush=True)
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
        print(
            f"[DBG] validation report success={report.success} retryable={[d.rule_id + ':' + d.path for d in retryable]}",
            flush=True,
        )

        if report.success:
            print("[DBG] validation successful -> returning HCL", flush=True)
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
            print("[DBG] auto-fill --yes: missing args ->", missing_args, flush=True)
            for a in missing_args:
                user_inputs[a] = '"AUTO_FILLED"'
            for blk in missing_blocks:
                user_inputs.setdefault(blk, {})
                user_inputs[blk]["placeholder"] = '"AUTO_BLOCK"'
            continue

        print("[DBG] interactive mode: prompting from deterministic diagnostics", flush=True)
        for a in missing_args:
            user_inputs[a] = input(f"Enter value for {a}: ")

        for blk in missing_blocks:
            user_inputs.setdefault(blk, {})
            nested = schema.get("block", {}).get("block_types", {}).get(blk, {}).get("block", {}).get("attributes", {})
            for aname, aspec in nested.items():
                if aspec.get("required") and aname not in user_inputs[blk]:
                    user_inputs[blk][aname] = input(f"{blk}.{aname}: ")
