from unittest import mock

from LCF.cloud_adapters.schema_manager import SchemaManager


def test_schema_manager_creates_workdir_and_skips_without_binary(tmp_path):
    workdir = tmp_path / "tofu-workdir"
    manager = SchemaManager(work_dir=str(workdir), tofu_bin="")

    assert workdir.exists()
    assert manager.get("aws_instance") == {"blocks": {}, "attributes": {}}


@mock.patch("LCF.cloud_adapters.schema_manager.subprocess.run")
def test_schema_manager_uses_configured_binary(mock_run, tmp_path):
    workdir = tmp_path / "tofu-workdir"

    # Return an empty valid schema response from tofu providers schema -json
    mock_run.side_effect = [
        mock.Mock(returncode=0, stdout="", stderr=""),
        mock.Mock(returncode=0, stdout='{"provider_schemas": {}}', stderr=""),
    ]

    SchemaManager(work_dir=str(workdir), tofu_bin="/custom/tofu")

    assert mock_run.call_args_list[0].args[0][0] == "/custom/tofu"
    assert mock_run.call_args_list[1].args[0][0] == "/custom/tofu"
