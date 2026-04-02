from LCF.resource_resolver import ResourceResolver


def test_canonicalize_identity_uses_static_alias_and_provider_hint() -> None:
    rr = ResourceResolver(db_path=":memory:")
    out = rr.canonicalize_identity(resource="vm", provider_hint="aws", logical_name="my-vm")

    assert out["_resolved"] == "aws_instance"
    assert out["_provider"] == "aws"
    assert out["_identity"] == {
        "provider": "aws",
        "resource_type": "aws_instance",
        "logical_name": "my-vm",
    }


def test_canonicalize_identity_returns_unmapped_failure_with_alternatives() -> None:
    rr = ResourceResolver(db_path=":memory:")
    out = rr.canonicalize_identity(resource="totally_unknown_service", provider_hint="auto")

    assert out["mode"] == "provider_native_type_unmapped"
    assert "alias_alternatives" in out
    assert "resolution_hint" in out
