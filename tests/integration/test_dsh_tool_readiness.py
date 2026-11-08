"""本地真实Harness的零模型调用就绪检查；端点固定为本地关闭端口，不使用凭证。"""

import json
from pathlib import Path

from deepseek_harness import DeepSeekHarness


def test_missing_mcp_tools_stop_before_model_request(tmp_path):
    plugin = Path("src/agent4pharma/infrastructure/llm/dsh_tool_readiness.cjs").resolve()
    patch = tmp_path / "patch.json"
    marker = tmp_path / "missing-tools"
    patch.write_text(
        json.dumps(
            [
                {
                    "insert": [
                        {
                            "id": "guard",
                            "name": plugin.as_uri(),
                            "config": {"required": ["get_stage_context"]},
                        }
                    ]
                }
            ]
        ),
        encoding="utf-8",
    )
    with DeepSeekHarness(
        cwd=str(tmp_path),
        profile="sdk-minimal",
        patches=(str(patch),),
        dsh_home=str(tmp_path / "home"),
        api_key="offline-test",
        base_url="http://127.0.0.1:1",
        env={"PHARMA_TOOL_GUARD_MARKER": str(marker)},
        request_timeout_seconds=10,
    ) as harness:
        result = harness.run("offline tool admission test")
    assert json.loads(marker.read_text())["missing"] == ["get_stage_context"]
    assert result.finish_reason != "completed"
    assert not any(e["type"] in {"assistant/message", "assistant/attempt"} for e in result.events)
