from LCF.provisioning.renderers.hcl_renderer import HCLIRRenderer


def test_hcl_ir_renderer_renders_scalars_maps_and_lists() -> None:
    schema = {
        "block": {
            "attributes": {
                "name": {"type": "string"},
                "tags": {"type": "map"},
                "cidrs": {"type": ["list", "string"]},
            }
        }
    }
    ir = {
        "name": "vm-1",
        "tags": {"env": "dev", "owner": "platform"},
        "cidrs": ["10.0.0.0/24", "10.0.1.0/24"],
    }

    hcl = HCLIRRenderer().render_resource("aws_instance", "vm one", ir, schema)

    assert 'resource "aws_instance" "vm_one" {' in hcl
    assert 'name = "vm-1"' in hcl
    assert "tags = {" in hcl
    assert 'cidrs = ["10.0.0.0/24", "10.0.1.0/24"]' in hcl


def test_hcl_ir_renderer_applies_nesting_mode_for_list_blocks() -> None:
    schema = {
        "block": {
            "block_types": {
                "network_interface": {
                    "nesting_mode": "list",
                    "block": {
                        "attributes": {
                            "subnet_id": {"type": "string"},
                        }
                    },
                }
            }
        }
    }
    ir = {
        "network_interface": [
            {"subnet_id": "subnet-a"},
            {"subnet_id": "subnet-b"},
        ]
    }

    hcl = HCLIRRenderer().render_resource("google_compute_instance", "vm", ir, schema)

    assert hcl.count("network_interface {") == 2
    assert 'subnet_id = "subnet-a"' in hcl
    assert 'subnet_id = "subnet-b"' in hcl
