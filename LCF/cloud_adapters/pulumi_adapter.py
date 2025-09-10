# LCF/cloud_adapters/pulumi_adapter.py
"""
Pulumi adapter for CloudBrew (hardened).
- Uses Pulumi Automation API if installed (recommended).
- Falls back to calling `pulumi` CLI via subprocess if not available.
- Exposes: plan(spec, stack_name), apply(spec, stack_name), destroy(stack_name)
- Streaming-friendly (yields logs) and raises detailed exceptions on error.
"""

import json
import os
import shutil
import tempfile
import subprocess
from typing import Dict, Generator, Optional, Iterable

# Try to import Pulumi automation API
try:
    import pulumi.automation as auto  # type: ignore
    _HAS_AUTOMATION = True
except Exception:
    _HAS_AUTOMATION = False


class PulumiAdapterError(Exception):
    pass


# ----------------------
# Project setup helpers
# ----------------------
def _write_spec(project_dir: str, spec: Dict):
    path = os.path.join(project_dir, "spec.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2)
    return path


def _make_project_template(project_dir: str, project_name: str = "cloudbrew-pulumi"):
    """
    Create a minimal Pulumi project layout that reads spec.json.
    Write a top-level __main__.py (pulumi runtime wants this), and also
    create a nested __main__ package with an __main__.py for older tests/tools.
    """
    os.makedirs(project_dir, exist_ok=True)

    # Pulumi.yaml (project manifest)
    with open(os.path.join(project_dir, "Pulumi.yaml"), "w", encoding="utf-8") as f:
        f.write(f"name: {project_name}\nruntime: python\n")

    # requirements.txt for Pulumi project
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
    # top-level
    with open(os.path.join(project_dir, "__main__.py"), "w", encoding="utf-8") as f:
        f.write(program)

    # nested package for legacy/test expectations
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
        program = None  # use work_dir runtime
        stack = _create_or_select_stack(project_dir, stack_name, program)

        if action == "preview":
            stack.preview(on_output=lambda _: None)
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
# Subprocess fallback
# ----------------------
def _stream_subprocess(cmd: Iterable[str], cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> Generator[str, None, None]:
    proc = subprocess.Popen(list(cmd), cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1, text=True)
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            yield line.rstrip("\n")
        proc.wait()
        if proc.returncode != 0:
            raise PulumiAdapterError(f"Command {' '.join(cmd)} failed with code {proc.returncode}")
    finally:
        proc.stdout.close()


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
        out_lines = []
        for line in proc.stdout:
            line = line.rstrip("\n")
            out_lines.append(line)
            yield line
        proc.wait()
        if proc.returncode != 0:
            raise PulumiAdapterError(f"Command {' '.join(cmd)} failed with code {proc.returncode}:\n{'\n'.join(out_lines)}")

    # init or select stack
    passphrase = env.get("PULUMI_CONFIG_PASSPHRASE") or env.get("PULUMI_CONFIG_PASSPHRASE_FILE")
    try:
        if passphrase:
            yield from _run_cmd_collect(["pulumi", "stack", "init", stack_name])
        else:
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
# Public API
# ----------------------
def _make_project_dir() -> str:
    return tempfile.mkdtemp(prefix="cloudbrew_pulumi_")


def plan(spec: Dict, stack_name: str = "dev") -> Generator[str, None, None]:
    project_dir = _make_project_dir()
    _make_project_template(project_dir)
    try:
        if _HAS_AUTOMATION:
            yield from _run_automation_op(project_dir, spec, stack_name, "preview")
        else:
            yield from _run_cli(project_dir, spec, stack_name, "preview")
    finally:
        shutil.rmtree(project_dir, ignore_errors=True)


def apply(spec: Dict, stack_name: str = "dev") -> Generator[str, None, None]:
    project_dir = _make_project_dir()
    _make_project_template(project_dir)
    try:
        if _HAS_AUTOMATION:
            yield from _run_automation_op(project_dir, spec, stack_name, "up")
        else:
            yield from _run_cli(project_dir, spec, stack_name, "up")
    finally:
        shutil.rmtree(project_dir, ignore_errors=True)


def destroy(stack_name: str = "dev") -> Generator[str, None, None]:
    project_dir = _make_project_dir()
    _make_project_template(project_dir)
    try:
        if _HAS_AUTOMATION:
            yield from _run_automation_op(project_dir, {}, stack_name, "destroy")
        else:
            yield from _run_cli(project_dir, {}, stack_name, "destroy")
    finally:
        shutil.rmtree(project_dir, ignore_errors=True)
