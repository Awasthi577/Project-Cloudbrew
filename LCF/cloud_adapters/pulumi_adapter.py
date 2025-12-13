# LCF/cloud_adapters/pulumi_adapter.py
"""
Pulumi adapter for CloudBrew (hardened).
- Supports Pulumi Automation API if installed.
- Falls back to Pulumi CLI subprocess execution otherwise.
- Exposes class PulumiAdapter with methods:
  create_instance, plan, apply_plan, destroy_instance.
- Also exposes module-level convenience functions plan(), apply(), destroy() and helpers used by tests.
"""

import json
import os
import shutil
import tempfile
import subprocess
from typing import Dict, Any, Generator, Optional, Iterable, List

from LCF import store

# Try to import Pulumi automation API
try:
    import pulumi.automation as auto  # type: ignore
    _HAS_AUTOMATION = True
except Exception:
    _HAS_AUTOMATION = False


class PulumiAdapterError(Exception):
    pass


# ----------------------
# Small helpers (test-friendly)
# ----------------------
def _make_project_dir(prefix: str = "cloudbrew_pulumi_") -> str:
    """Create and return a temp project dir for pulumi runs."""
    d = tempfile.mkdtemp(prefix=prefix)
    # ensure project template exists
    _make_project_template(d)
    return d


def _write_spec(project_dir: str, spec: Dict):
    path = os.path.join(project_dir, "spec.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2)
    return path


def _make_project_template(project_dir: str, project_name: str = "cloudbrew-pulumi"):
    """
    Ensure a minimal Pulumi python project template exists in project_dir.
    This creates Pulumi.yaml and a small __main__ program both at top-level
    and inside a __main__ package dir (tests expect nested __main__/__main__.py).
    """
    os.makedirs(project_dir, exist_ok=True)

    # Pulumi.yaml
    with open(os.path.join(project_dir, "Pulumi.yaml"), "w", encoding="utf-8") as f:
        f.write(f"name: {project_name}\nruntime: python\n")

    # requirements.txt
    with open(os.path.join(project_dir, "requirements.txt"), "w", encoding="utf-8") as f:
        f.write("pulumi\n")

    program = """\
import json
import os
from pulumi import export

spec_path = os.path.join(os.getcwd(), "spec.json")
spec = {}
try:
    with open(spec_path, "r", encoding="utf-8") as f:
        spec = json.load(f)
except Exception:
    spec = {"resources": []}

export("cloudbrew_spec_summary", len(spec.get("resources", [])))
"""

    # top-level __main__.py
    with open(os.path.join(project_dir, "__main__.py"), "w", encoding="utf-8") as f:
        f.write(program)

    # nested package __main__/__main__.py (some Pulumi test harnesses look here)
    nested_dir = os.path.join(project_dir, "__main__")
    os.makedirs(nested_dir, exist_ok=True)
    with open(os.path.join(nested_dir, "__main__.py"), "w", encoding="utf-8") as f:
        f.write(program)


# ----------------------
# Automation mode
# ----------------------
if _HAS_AUTOMATION:

    def _create_or_select_stack(project_dir: str, stack_name: str, program=None):
        try:
            return auto.create_or_select_stack(stack_name=stack_name, work_dir=project_dir, program=program)
        except Exception:
            try:
                return auto.select_stack(stack_name=stack_name, work_dir=project_dir)
            except Exception:
                return auto.create_stack(stack_name=stack_name, work_dir=project_dir, program=program)

    def _run_automation_op(project_dir: str, spec: Dict, stack_name: str, action: str) -> Generator[str, None, None]:
        _write_spec(project_dir, spec)
        program = None
        stack = _create_or_select_stack(project_dir, stack_name, program)

        if action == "preview":
            for o in stack.preview(on_output=lambda _: None):
                # automation API preview can optionally yield outputs; tests expect generator behavior
                yield str(o)
            yield "Preview completed."
            return
        if action == "up":
            result = stack.up(on_output=lambda _: None)
            summary = getattr(result, "summary", None)
            yield f"Apply succeeded: summary={getattr(summary, 'resource_changes', 'unknown')}"
            return
        if action == "destroy":
            stack.destroy(on_output=lambda _: None)
            yield "Destroy completed."
            return

        raise PulumiAdapterError(f"Unknown automation action: {action}")


# ----------------------
# Subprocess fallback (signature test-friendly)
# ----------------------
def _stream_subprocess(cmd: Iterable[str], cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> Generator[str, None, None]:
    """
    Stream subprocess stdout/stderr. Accepts cwd and env (both optional) so tests can call it easily.
    """
    proc = subprocess.Popen(list(cmd), cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1, text=True)
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            yield line.rstrip("\n")
        proc.wait()
        if proc.returncode != 0:
            # collect tail for easier debugging
            raise PulumiAdapterError(f"Command {' '.join(cmd)} failed with code {proc.returncode}")
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass


def _run_cli(project_dir: str, spec: Dict, stack_name: str, action: str) -> Generator[str, None, None]:
    _write_spec(project_dir, spec)
    if not os.path.exists(os.path.join(project_dir, "Pulumi.yaml")):
        _make_project_template(project_dir)

    env = os.environ.copy()
    try:
        import sys
        env["PULUMI_PYTHON_CMD"] = sys.executable
    except Exception:
        pass

    def _run_cmd_collect(cmd):
        proc = subprocess.Popen(list(cmd), cwd=project_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True, bufsize=1)
        assert proc.stdout is not None
        out_lines: List[str] = []
        for line in proc.stdout:
            line = line.rstrip("\n")
            out_lines.append(line)
            yield line
        proc.wait()
        if proc.returncode != 0:
            raise PulumiAdapterError(f"Command {' '.join(cmd)} failed with code {proc.returncode}:\n{''.join(out_lines)}")

    try:
        yield from _run_cmd_collect(["pulumi", "stack", "init", stack_name, "--secrets-provider", "plaintext"])
    except PulumiAdapterError:
        yield f"stack {stack_name} already exists or init failed; selecting"
        yield from _run_cmd_collect(["pulumi", "stack", "select", stack_name])

    if action == "preview":
        yield from _run_cmd_collect(["pulumi", "preview", "--non-interactive"])
    elif action == "up":
        yield from _run_cmd_collect(["pulumi", "up", "--yes", "--non-interactive"])
    elif action == "destroy":
        yield from _run_cmd_collect(["pulumi", "destroy", "--yes", "--non-interactive"])
    else:
        raise PulumiAdapterError(f"Unknown action: {action}")


# ----------------------
# PulumiAdapter Class
# ----------------------
class PulumiAdapter:
    def __init__(self, db_path: Optional[str] = None):
        self.store = store.SQLiteStore(db_path)

    def _run_action(self, spec: Dict, stack_name: str, action: str) -> List[str]:
        project_dir = _make_project_dir()
        try:
            if _HAS_AUTOMATION:
                out = list(_run_automation_op(project_dir, spec, stack_name, action))
            else:
                out = list(_run_cli(project_dir, spec, stack_name, action))
            return out
        finally:
            shutil.rmtree(project_dir, ignore_errors=True)

    def create_instance(self, logical_id: str, spec: Dict[str, Any], plan_only: bool = False) -> Dict[str, Any]:
        stack_name = logical_id
        if plan_only:
            out = self._run_action(spec, stack_name, "preview")
            return {"plan_id": f"pulumi-{stack_name}", "diff": "\n".join(out)}
        else:
            out = self._run_action(spec, stack_name, "up")
            adapter_id = f"pulumi-{stack_name}"
            inst = {
                "logical_id": logical_id,
                "adapter": "pulumi",
                "adapter_id": adapter_id,
                "spec": spec,
                "state": "running",
                "created_at": int(__import__("time").time()),
            }
            self.store.upsert_instance(inst)
            return {"success": True, "adapter_id": adapter_id, "output": "\n".join(out)}

    def plan(self, logical_id: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        out = self._run_action(spec, logical_id, "preview")
        return {"plan_id": f"pulumi-{logical_id}", "diff": "\n".join(out)}

    def apply_plan(self, plan_id: str) -> Dict[str, Any]:
        # plan_id is "pulumi-<stack_name>"
        stack_name = plan_id.replace("pulumi-", "")
        out = self._run_action({}, stack_name, "up")
        adapter_id = f"pulumi-{stack_name}"
        return {"success": True, "adapter_id": adapter_id, "output": "\n".join(out)}

    def destroy_instance(self, adapter_id: str) -> bool:
        # adapter_id is "pulumi-<stack_name>"
        stack_name = adapter_id.replace("pulumi-", "")
        _ = self._run_action({}, stack_name, "destroy")
        self.store.delete_instance_by_adapter_id(adapter_id)
        return True


# ----------------------
# Module-level convenience wrappers (tests and CLI expect these)
# ----------------------
def plan(spec: Dict[str, Any], stack: str = "dev") -> Generator[str, None, None]:
    pa = PulumiAdapter()
    # stream the preview
    out = pa.plan(stack, spec)
    # if pa.plan returns dict, yield text; if it streams, adapt accordingly
    diff = out.get("diff") if isinstance(out, dict) else out
    if isinstance(diff, str):
        for ln in diff.splitlines():
            yield ln
    else:
        # fallback: send entire repr
        yield json.dumps(out)


def apply(spec: Dict[str, Any], stack: str = "dev") -> Generator[str, None, None]:
    pa = PulumiAdapter()
    res = pa.create_instance(stack, spec, plan_only=False)
    out = res.get("output") or ""
    for ln in str(out).splitlines():
        yield ln


def destroy(stack: str) -> Generator[str, None, None]:
    pa = PulumiAdapter()
    # call destroy_instance and stream a completion line
    pa.destroy_instance(f"pulumi-{stack}")
    yield f"destroyed:{stack}"
