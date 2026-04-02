from pathlib import Path

from LCF.provisioning.validator import ProvisioningValidator


def test_tier1_reports_required_and_type_mismatches():
    schema = {
        "block": {
            "attributes": {
                "name": {"type": "string", "required": True},
                "count": {"type": "number", "required": False},
            },
            "block_types": {
                "network_interface": {
                    "nesting_mode": "list",
                    "min_items": 1,
                    "max_items": 1,
                    "block": {
                        "attributes": {
                            "subnet_id": {"type": "string", "required": True},
                        }
                    },
                }
            },
        }
    }

    report = ProvisioningValidator().validate(schema=schema, values={"count": "oops"})

    rule_ids = {d.rule_id for d in report.diagnostics}
    assert "SCHEMA_REQUIRED_ATTRIBUTE_MISSING" in rule_ids
    assert "SCHEMA_ATTRIBUTE_TYPE_MISMATCH" in rule_ids
    assert "SCHEMA_REQUIRED_BLOCK_MISSING" in rule_ids


def test_tier2_extracts_missing_argument_and_block_from_tofu_output(monkeypatch, tmp_path: Path):
    class Proc:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    calls = []

    def fake_run(cmd, cwd, capture_output, text, env):
        calls.append(cmd)
        if cmd[1] == "init":
            return Proc(0, stdout="initialized")
        if cmd[1] == "validate":
            return Proc(
                1,
                stderr='The argument "ami" is required\nA block "network_interface" is required',
            )
        return Proc(0)

    monkeypatch.setattr("LCF.provisioning.validator.subprocess.run", fake_run)

    v = ProvisioningValidator(tofu_bin="tofu")
    report = v.validate(schema={"block": {}}, values={}, rendered_hcl='resource "x" "y" {}', workdir=tmp_path)

    assert report.success is False
    assert [c[1] for c in calls] == ["init", "validate"]
    assert any(d.rule_id == "TOFU_REQUIRED_ARGUMENT_MISSING" and d.path == "ami" for d in report.diagnostics)
    assert any(d.rule_id == "TOFU_REQUIRED_BLOCK_MISSING" and d.path == "network_interface" for d in report.diagnostics)
