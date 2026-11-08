import pytest

from agent4pharma.infrastructure.llm.dsh_native_support import configured_sharp_native


def test_native_runtime_configuration_is_explicit_and_fail_closed(tmp_path):
    import json
    import os

    assert configured_sharp_native(tmp_path) is None
    (tmp_path / "dsh-runtime.json").write_text(json.dumps({"windows_sharp_native": "relative.node"}))
    if os.name == "nt":
        with pytest.raises(ValueError, match="绝对路径"):
            configured_sharp_native(tmp_path)
    else:
        assert configured_sharp_native(tmp_path) is None
