# LCF/cloud_adapters/dynamic_resource_creator.py
from pathlib import Path
import inspect
from LCF.cloud_adapters.tofu_validator import run_tofu_validate
from LCF.cloud_adapters.opentofu_adapter import OpenTofuAdapter

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

        print("[DBG] running tofu validate...", flush=True)
        result = run_tofu_validate(tmp)
        print(f"[DBG] validate result success={result.success} missing_args={result.missing_args} missing_blocks={result.missing_blocks}", flush=True)

        if not result.success and not getattr(result, "missing_args", []) and not getattr(result, "missing_blocks", []):
            return {
                "success": False,
                "hcl": hcl,
                "validation_raw": getattr(result, "raw_output", None) or getattr(result, "stderr", None) or "Validation failed with no missing args/blocks."
            }

        if result.success:
            print("[DBG] validation successful -> returning HCL", flush=True)
            return {"success": True, "hcl": hcl}

        if getattr(args, "yes", False):
            print("[DBG] auto-fill --yes: missing args ->", result.missing_args, flush=True)
            for a in result.missing_args:
                user_inputs[a] = '"AUTO_FILLED"'
            for blk in result.missing_blocks:
                user_inputs.setdefault(blk, {})
                user_inputs[blk]["placeholder"] = '"AUTO_BLOCK"'
            continue

        print("[DBG] interactive mode: will prompt for missing fields", flush=True)
        for a in result.missing_args:
            user_inputs[a] = input(f"Enter value for {a}: ")

        for blk in result.missing_blocks:
            user_inputs.setdefault(blk, {})
            nested = schema.get("block", {}).get("block_types", {}).get(blk, {}).get("block", {}).get("attributes", {})
            for aname, aspec in nested.items():
                if aspec.get("required"):
                    user_inputs[blk][aname] = input(f"{blk}.{aname}: ")
