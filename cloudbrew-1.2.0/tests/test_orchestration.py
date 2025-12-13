import json
import os

from LCF.orchestration import create_from_spec
from LCF.utils import load_state


def test_create_vm_and_state(tmp_path, monkeypatch):
    # ensure clean state
    state_file = tmp_path / ".cloudbrew_state.json"
    monkeypatch.chdir(tmp_path)
    # copy sample spec
    sample = {
        "resources": [
            {
                "type": "vm",
                "name": "testvm",
                "image": "ubuntu",
                "size": "micro",
                "region": "us-east-1",
            }
        ]
    }
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "sample.yml").write_text(
        json.dumps(sample).replace('"', "'")
    )  # valid simple YAML-ish

    # Instead of relying on YAML, call create_from_spec directly
    res = create_from_spec(sample)
    assert isinstance(res, list)
    st = load_state()
    assert (
        any(
            k.startswith("vm:testvm") or k == "vm:testvm" or "vm:testvm" in st
            for k in [*st.keys()]
        )
        or "vm:testvm" in st
    )

    # clean up
    if state_file.exists():
        os.remove(state_file)
