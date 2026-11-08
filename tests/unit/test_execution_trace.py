"""通用追踪契约、DSH事件关联与MCP观测的离线回归，不调用模型。"""

import asyncio
import json
from types import SimpleNamespace

from deepseek_harness.models import Notification

from agent4pharma.entrypoints.mcp.observed_tools import observed_tool
from agent4pharma.infrastructure.llm.dsh_execution_trace import DshExecutionTrace
from agent4pharma.runtime.execution_trace import safe_trace_value
from agent4pharma.runtime.workflow_observation import WorkflowObservation


class Sink:
    def __init__(self):
        self.events = []

    def append(self, event):
        self.events.append(event)


def event(kind, data, seq=0, **extra):
    return dict(type=kind, data=data, seq=seq, **extra)


def test_legacy_observation_round_trip_omits_new_metadata():
    old = dict(
        id="old",
        subject_ref="task",
        stage="content",
        operation="read",
        status="succeeded",
        created_at="2026-11-03T00:00:00+00:00",
    )
    observation = WorkflowObservation.model_validate(old)
    assert "execution" not in observation.model_dump()


def test_model_tools_capture_exact_call_identity_visible_input_and_output():
    sink = Sink()
    trace = DshExecutionTrace(sink, "task", "a-custom-agent", "session", "model-x", attempt=2)
    trace.begin({"instruction": "actual input"})
    trace.event(event("step/start", dict(turn=1, step=1)))
    trace.event(event("request/header", {"header": {"config": {"model": "model-x"}, "tools": []}}))
    trace.event(
        event(
            "system/message",
            {"message": {"role": "system", "content": "system rules"}},
            seq=1,
            surfaceOp="append",
        )
    )
    trace.event(
        event(
            "user/message",
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            seq=2,
            surfaceOp="append",
        )
    )
    trace.event(
        event(
            "assistant/message",
            dict(
                turn=1,
                step=1,
                message={
                    "role": "assistant",
                    "content": [
                        {"type": "reasoning", "text": "hidden-internal"},
                        {"type": "text", "text": "public answer"},
                    ],
                },
            ),
            seq=3,
            surfaceOp="append",
        )
    )
    trace.event(
        event(
            "tool/call",
            dict(
                turn=1,
                step=1,
                callId="call-exact",
                name="arbitrary_tool",
                arguments='{"query":"actual query"}',
            ),
        )
    )
    trace.event(
        event(
            "tool/result",
            {"message": {"toolCallId": "call-exact", "content": "actual result"}},
            seq=4,
            surfaceOp="append",
        )
    )
    trace.finish(SimpleNamespace(finish_reason="completed", final_response="final public reply"))
    models = [e for e in sink.events if e.execution.kind == "model"]
    model_input = json.loads(models[-2].input_json)
    assert model_input["context_complete"] and len(model_input["messages"]) == 2
    assert "public answer" in models[-1].output_json
    tools = [e for e in sink.events if e.execution.kind == "tool"]
    assert len(tools) == 2 and tools[0].execution.span_id == tools[1].execution.span_id
    assert tools[0].execution.parent_span_id == models[-1].execution.span_id
    assert tools[0].execution.call_id == "call-exact" and tools[1].execution.duration_ms is not None
    assert json.loads(tools[0].input_json) == {"query": "actual query"}
    serialized = "".join(e.model_dump_json() for e in sink.events)
    assert "hidden-internal" not in serialized
    assert sink.events[0].execution.agent_id == "a-custom-agent"


def test_surface_replacement_reuses_actual_compacted_context_and_marks_missing_history():
    sink = Sink()
    trace = DshExecutionTrace(sink, "task", "agent", "session", "model")
    trace.event(event("user/message", {"role": "user", "content": "before"}, seq=1, surfaceOp="append"))
    trace.event(
        event(
            "user/message",
            {"role": "user", "content": "compacted"},
            seq=3,
            surfaceOp={"op": "replace", "startSeq": 1, "endSeq": 1},
        )
    )
    trace.event(event("step/start", dict(turn=1, step=2)))
    assert "compacted" in sink.events[-1].input_json and "before" not in sink.events[-1].input_json
    trace.event(
        event(
            "user/message",
            {"content": "unknown replacement"},
            seq=4,
            surfaceOp={"op": "replace", "startSeq": 99, "endSeq": 100},
        )
    )
    trace.event(event("step/start", dict(turn=1, step=3)))
    assert not json.loads(sink.events[-1].input_json)["context_complete"]


def test_unrelated_session_and_unknown_tool_result_are_not_paired():
    sink = Sink()
    trace = DshExecutionTrace(sink, "task", "agent", "owned", "model")
    trace.observe(
        Notification(
            method="session.event",
            payload={
                "sessionId": "other-session",
                "event": event("step/start", dict(turn=1, step=1)),
            },
        )
    )
    trace.event(event("tool/result", {"message": {"toolCallId": "unobserved", "content": "unknown"}}))
    assert not sink.events


def test_redaction_is_recursive_and_unknown_business_fields_survive():
    payload = {
        "Authorization": "Bearer secret",
        "nested": {"api_key": "secret", "text": "real secret"},
        "business_value": 7,
        "image": {"type": "image", "data": "binary"},
        "stream": [{"type": "reasoning-delta", "value": "hidden"}],
    }
    safe = safe_trace_value(payload, ("secret",))
    encoded = json.dumps(safe)
    assert "hidden" not in encoded and "binary" not in encoded and "secret" not in encoded
    assert safe["business_value"] == 7


def test_async_mcp_observations_have_shared_span_and_do_not_record_unawaited_coroutines():
    sink = Sink()

    async def arbitrary(value: int) -> dict:
        return {"value": value + 1}

    result = asyncio.run(observed_tool(arbitrary, sink, "task", "custom-agent", attempt=3)(8))
    assert result == {"value": 9}
    start, end = sink.events
    assert start.execution.span_id == end.execution.span_id
    assert start.execution.parent_span_id == "agent:task:3"
    assert start.execution.server == "pharma" and end.execution.duration_ms is not None
    assert json.loads(end.output_json) == result


def test_mcp_inherits_only_its_owned_agent_environment(monkeypatch):
    sink = Sink()
    trace = DshExecutionTrace(sink, "owned-task", "agent", "unique-session", "model")
    for key, value in trace.environment().items():
        monkeypatch.setenv(key, value)

    def tool() -> str:
        return "actual output"

    asyncio.run(observed_tool(tool, sink, "owned-task", "agent")())
    assert sink.events[-1].execution.parent_span_id == trace.identity.span_id
    assert sink.events[-1].execution.session_id == "unique-session"
    asyncio.run(observed_tool(tool, sink, "other-task", "agent")())
    assert sink.events[-1].execution.parent_span_id != trace.identity.span_id


def test_root_run_preserves_failure_and_filters_foreign_notification():
    sink = Sink()
    trace = DshExecutionTrace(sink, "task", "agent", "owned", "model")

    class Harness:
        def run(self, instruction, **options):
            assert instruction == "instruction" and options["session_id"] == "owned"
            raise RuntimeError("provider diagnostics must never persist")

    import pytest

    with pytest.raises(RuntimeError):
        trace.run(Harness(), "instruction")
    assert sink.events[-1].status == "failed"
    assert json.loads(sink.events[-1].output_json) == {"error_type": "RuntimeError"}
    assert "provider diagnostics" not in sink.events[-1].output_json


def test_mcp_structured_results_and_error_flags_are_preserved():
    from mcp.types import CallToolResult, TextContent

    sink = Sink()

    def tool() -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text="tool explanation")],
            structuredContent={"arbitrary_field": 5},
            isError=True,
        )

    result = asyncio.run(observed_tool(tool, sink, "task", "agent")())
    assert result.is_error and sink.events[-1].status == "failed"
    encoded = json.loads(sink.events[-1].output_json)
    assert encoded["structuredContent"] == {"arbitrary_field": 5} and encoded["isError"]


def test_mcp_host_crash_has_safe_diagnostic_id_through_actual_tool_protocol():
    import sqlite3

    import pytest
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.server.mcpserver.tools.base import Tool

    sink = Sink()

    def submit() -> str:
        raise sqlite3.IntegrityError("private SQL, path and credentials")

    tool = Tool.from_function(observed_tool(submit, sink, "task", "content"))
    with pytest.raises(ToolError) as error:
        asyncio.run(tool.run({}, None))
    message = str(error.value)
    assert "IntegrityError" in message and "诊断编号" in message
    assert sink.events[0].execution.call_id in message
    assert "private" not in message and "private" not in sink.events[-1].detail
    assert sink.events[-1].status == "failed" and sink.events[-1].detail == "IntegrityError"


def test_mcp_expected_validation_keeps_field_explanation():
    import pytest
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.server.mcpserver.tools.base import Tool

    def submit() -> str:
        raise ValueError("findings[0].source_span：证据版本不匹配")

    tool = Tool.from_function(observed_tool(submit, Sink(), "task", "content"))
    with pytest.raises(ToolError, match=r"findings\[0\].source_span"):
        asyncio.run(tool.run({}, None))
