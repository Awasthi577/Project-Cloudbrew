# LCF/cloud_adapters/protocol.py
from typing import Protocol, Dict, Any, List, Optional, TypedDict

class InstanceInfo(TypedDict):
    logical_id: str
    adapter: str
    adapter_id: str
    state: str
    spec: Dict

class PlanResult(TypedDict, total=False):
    plan_id: str
    diff: Optional[str]

class ApplyResult(TypedDict, total=False):
    success: bool
    adapter_id: Optional[str]
    output: Optional[str]

class ComputeAdapter(Protocol):
    def create_instance(self, logical_id: str, spec: Dict, plan_only: bool = False) -> PlanResult | ApplyResult: ...
    def destroy_instance(self, adapter_id: str) -> bool: ...
    def list_instances(self, filter: Dict | None = None) -> List[InstanceInfo]: ...
    def plan(self, logical_id: str, spec: Dict) -> PlanResult: ...
    def apply_plan(self, plan_id: str) -> ApplyResult: ...
