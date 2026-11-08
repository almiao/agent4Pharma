"""前端只读投影的无网络契约回归；Node 可用时纳入默认 pytest。"""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("script", ["test_flow_projection.mjs", "test_workspace_model.mjs"])
def test_flow_projection_scope_and_drilldown_contract(script):
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node unavailable; run tests/frontend/test_flow_projection.mjs separately")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [executable, "--test", str(root / "tests/frontend" / script)],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
