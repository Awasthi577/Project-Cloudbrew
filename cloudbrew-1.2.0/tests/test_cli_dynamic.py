from typer.testing import CliRunner
from LCF.cli import app
from unittest.mock import patch

runner = CliRunner()

def test_unknown_command_routes_to_resolver(monkeypatch):
    # monkeypatch ResourceResolver.resolve to return predictable payload
    from LCF.resource_resolver import ResourceResolver
    monkeypatch.setattr("LCF.cli.ResourceResolver", ResourceResolver)
    # monkeypatch resolve method to return resolved info
    monkeypatch.setattr("LCF.resource_resolver.ResourceResolver.resolve", lambda self, resource, provider: {"_resolved": "aws_s3_bucket", "_provider": "opentofu"})
    
    result = runner.invoke(app, ["bucket", "my-bucket", "--region", "us-east-1"])
    
    assert result.exit_code == 0
    assert "dynamic-fallback" in result.stdout
    assert "aws_s3_bucket" in result.stdout