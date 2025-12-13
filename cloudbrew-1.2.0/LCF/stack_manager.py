import json
import logging
import os
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import time

try:
    from jinja2 import Template
    HAS_JINJA = True
except ImportError:
    from string import Template
    HAS_JINJA = False

from LCF.cloud_adapters.opentofu_adapter import OpenTofuAdapter

logger = logging.getLogger(__name__)

@dataclass
class StackResult:
    stack_name: str
    success: bool
    resources_created: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    elapsed_time: float = 0.0

class StackManager:
    DEFAULT_BLUEPRINTS = {
        "lamp": {
            "description": "Linux, Apache, MySQL, PHP Stack",
            "resources": [
                {
                    "name": "{{ stack_name }}-web",
                    "type": "vm",
                    "image": "ubuntu-22.04",
                    "size": "small",
                    "tags": {"role": "webserver", "stack": "{{ stack_name }}", "env": "{{ env }}"}
                },
                {
                    "name": "{{ stack_name }}-db",
                    "type": "vm",
                    "image": "mysql-8",
                    "size": "medium",
                    "tags": {"role": "database", "stack": "{{ stack_name }}", "env": "{{ env }}"}
                }
            ]
        },
        "k8s-min": {
            "description": "Minimal K8s Cluster",
            "resources": [
                {"name": "{{ stack_name }}-cp", "type": "vm", "size": "medium", "tags": {"role": "cp"}},
                {"name": "{{ stack_name }}-node-1", "type": "vm", "size": "small", "tags": {"role": "worker"}}
            ]
        }
    }

    def __init__(self, blueprints_dir: str = "blueprints"):
        self.blueprints_dir = blueprints_dir
        if not os.path.exists(blueprints_dir):
            try:
                os.makedirs(blueprints_dir, exist_ok=True)
            except OSError:
                pass

    def list_stacks(self) -> Dict[str, str]:
        stacks = {k: v["description"] for k, v in self.DEFAULT_BLUEPRINTS.items()}
        
        # Load from disk
        if os.path.exists(self.blueprints_dir):
            for f in os.listdir(self.blueprints_dir):
                if f.endswith(".json") or f.endswith(".yaml"):
                    name = os.path.splitext(f)[0]
                    stacks[name] = f"File-based blueprint: {f}"
        return stacks

    def _render_template(self, raw_str: str, context: Dict[str, Any]) -> str:
        if HAS_JINJA:
            return Template(raw_str).render(**context)
        else:
            from string import Template as StringTemplate
            normalized = raw_str.replace("{{ ", "${").replace(" }}", "}")
            return StringTemplate(normalized).safe_substitute(**context)

    def _hydrate_blueprint(self, blueprint_name: str, stack_name: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        blueprint_content = self.DEFAULT_BLUEPRINTS.get(blueprint_name)
        
        if not blueprint_content and os.path.exists(self.blueprints_dir):
            f_path = os.path.join(self.blueprints_dir, f"{blueprint_name}.json")
            if os.path.exists(f_path):
                with open(f_path, 'r') as f:
                    blueprint_content = json.load(f)
        
        if not blueprint_content:
            raise ValueError(f"Blueprint '{blueprint_name}' not found.")


        context = copy.deepcopy(params)
        context["stack_name"] = stack_name
        context.setdefault("env", "dev")

        
        hydrated_resources = []
        raw_resources = blueprint_content.get("resources", [])
        
        try:
            raw_json_str = json.dumps(raw_resources)
            rendered_json_str = self._render_template(raw_json_str, context)
            hydrated_resources = json.loads(rendered_json_str)
        except Exception as e:
            logger.error(f"Template rendering failed: {e}")
            raise ValueError(f"Failed to render blueprint template: {e}")

        global_region = params.get("region")
        if global_region:
            for res in hydrated_resources:
                if "region" not in res:
                    res["region"] = global_region

        return hydrated_resources

    def deploy_stack(self, blueprint_name: str, stack_name: str, params: Dict[str, Any], dry_run: bool = False) -> StackResult:
        start_time = time.time()
        result = StackResult(stack_name=stack_name, success=True)
        
        try:
            resources = self._hydrate_blueprint(blueprint_name, stack_name, params)
        except Exception as e:
            result.success = False
            result.errors.append(str(e))
            return result

        adapter = OpenTofuAdapter()
        futures = {}
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            for res_spec in resources:
                res_name = res_spec.get("name", "unknown")
                
                if dry_run:
                    future = executor.submit(adapter.plan, res_name, res_spec)
                else:
                    future = executor.submit(adapter.create_instance, res_name, res_spec, plan_only=False)
                
                futures[future] = res_name

            for future in as_completed(futures):
                res_name = futures[future]
                try:
                    op_result = future.result()
                    if op_result.get("success", False) or "plan_output" in op_result or "diff" in op_result:
                        result.resources_created.append(res_name)
                        logger.info(f"Resource {res_name} processed successfully via OpenTofu.")
                    else:
                        error_msg = op_result.get("error", "Unknown adapter error")
                        result.errors.append(f"{res_name}: {error_msg}")
                        result.success = False
                        logger.error(f"Resource {res_name} failed: {error_msg}")
                except Exception as e:
                    result.errors.append(f"{res_name}: Exception {str(e)}")
                    result.success = False
                    logger.error(f"Resource {res_name} raised exception: {e}")

        result.elapsed_time = time.time() - start_time
        return result