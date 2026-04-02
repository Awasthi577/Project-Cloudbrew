from __future__ import annotations

import json
from pathlib import Path

import pytest

from LCF.provisioning.renderers.hcl_renderer import HCLIRRenderer

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.mark.parametrize(
    "resource_type,logical_name,schema_file,ir_file,golden_file",
    [
        (
            "aws_instance",
            "web-simple",
            "schemas/aws_instance_excerpt.json",
            "ir/aws_instance_simple.json",
            "golden/aws_instance_simple.tf",
        ),
        (
            "google_compute_instance",
            "gce-nested",
            "schemas/gcp_compute_instance_excerpt.json",
            "ir/gcp_instance_nested.json",
            "golden/gcp_instance_nested.tf",
        ),
        (
            "azurerm_linux_virtual_machine",
            "vm-linux",
            "schemas/azure_linux_vm_excerpt.json",
            "ir/azure_linux_vm.json",
            "golden/azure_linux_vm.tf",
        ),
    ],
)
def test_hcl_renderer_matches_golden_outputs(
    resource_type: str,
    logical_name: str,
    schema_file: str,
    ir_file: str,
    golden_file: str,
) -> None:
    schema = json.loads((FIXTURES / schema_file).read_text(encoding="utf-8"))
    ir = json.loads((FIXTURES / ir_file).read_text(encoding="utf-8"))
    expected_hcl = (FIXTURES / golden_file).read_text(encoding="utf-8").strip()

    rendered = HCLIRRenderer().render_resource(
        resource_type=resource_type,
        logical_name=logical_name,
        ir=ir,
        schema=schema,
    )

    assert rendered.strip() == expected_hcl
