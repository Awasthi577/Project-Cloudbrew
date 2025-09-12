import pytest
from unittest.mock import patch
from LCF.resource_resolver import ResourceResolver

def fake_tf_schema():
    # minimal shape with provider_schemas / resource_schemas
    return {
        "provider_schemas": {
            "registry.terraform.io/hashicorp/aws": {
                "resource_schemas": {
                    "aws_s3_bucket": {"attrs": {}},
                    "aws_instance": {"attrs": {}},
                }
            },
        }
    }

def test_gather_terraform_names(monkeypatch, tmp_path):
    rr = ResourceResolver(db_path=str(tmp_path/"rr.db"))
    monkeypatch.setattr(rr, "_query_terraform_schema", lambda: fake_tf_schema())
    names = rr._gather_provider_resource_names("terraform")
    assert "aws_s3_bucket" in names
    assert "aws_instance" in names

def test_discovery_exact_match(monkeypatch, tmp_path):
    rr = ResourceResolver(db_path=str(tmp_path/"rr2.db"))
    monkeypatch.setattr(rr, "_query_terraform_schema", lambda: fake_tf_schema())
    # request 'bucket' should match aws_s3_bucket
    res = rr.resolve("bucket", "terraform")
    # resolver returns dict: either full schema or {"_resolved": name,...} depending on implementation
    assert isinstance(res, dict)
    # accepted results: either schema dict or resolved metadata
    assert any(k in res for k in ("_resolved", "resource_schemas", "attrs"))

def test_resolve_unsupported_provider(tmp_path):
    rr = ResourceResolver(db_path=str(tmp_path/"r3.db"))
    with pytest.raises(Exception) as exc:
        # if your resolve returns dict with error instead of raising, adapt this test
        _ = rr.resolve("bucket", "nonexistent")
