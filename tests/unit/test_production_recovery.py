"""非法JSON仅纠正一次，公开错误只读取结构化代码，不执行模型表达式。"""

from types import SimpleNamespace

import pytest

from agent4pharma.infrastructure.llm.dsh_execution_trace import DshExecutionTrace
from agent4pharma.runtime.production_failure import malformed_tool_response


class Sink:
    def __init__(self):
        self.events = []

    def append(self, event):
        self.events.append(event)


def result(code="MALFORMED_RESPONSE"):
    return SimpleNamespace(
        finish_reason="error",
        final_response="",
        events=[
            {
                "type": "assistant/attempt",
                "data": {
                    "stream": [{"type": "finish", "reason": {"kind": "error", "failure": {"code": code}}}]
                },
            }
        ],
    )


@pytest.mark.parametrize("code,count", [("MALFORMED_RESPONSE", 2), ("HTTP_ERROR", 1)])
def test_same_session_recovery_is_bounded_and_only_for_malformed(code, count):
    calls = []

    class Harness:
        def run(self, text, **kwargs):
            calls.append((text, kwargs["session_id"]))
            return result(code)

    trace = DshExecutionTrace(Sink(), "task", "content", "session", "deepseek-flash")
    assert trace.run(Harness(), "write", repair_malformed=lambda: True).finish_reason == "error"
    assert len(calls) == count
    assert all(session == "session" for _, session in calls)
    if count == 2:
        assert "禁止代码表达式" in calls[1][0]


def test_no_recovery_by_default_or_for_strings_inside_model_text():
    calls = []
    harness = SimpleNamespace(run=lambda *a, **k: calls.append(1) or result())
    trace = DshExecutionTrace(Sink(), "task", "content", "session", "deepseek-flash")
    trace.run(harness, "write")
    assert len(calls) == 1
    trace.run(harness, "write", repair_malformed=lambda: False)
    assert len(calls) == 2
    assert not malformed_tool_response(
        [{"type": "assistant/attempt", "data": {"text": "403 MALFORMED_RESPONSE", "stream": []}}]
    )
