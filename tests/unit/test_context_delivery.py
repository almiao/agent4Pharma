"""宿主回执到SDK可见上下文的明确关联，不把离线事件当真实模型质量验证。"""

import asyncio
import json

import pytest

from agent4pharma.entrypoints.mcp.observed_tools import observed_tool
from agent4pharma.infrastructure.llm.dsh_execution_trace import DshExecutionTrace
from agent4pharma.runtime.context_records import ContextManifest, ContextPayload, context_manifest
from agent4pharma.runtime.execution_trace import ExecutionIdentity
from agent4pharma.runtime.production_execution import ProductionLimits, ProductionTask
from agent4pharma.runtime.workflow_observation import ObservationPage, WorkflowObservation
from agent4pharma.workflows.production_context_reports import inspect_task_context


class Sink:
    def __init__(self):
        self.events = []

    def append(self, event):
        self.events.append(event)

    def page(self, subjects, after=0, limit=50, *, operations=()):
        rows = [
            (i, e)
            for i, e in enumerate(self.events, 1)
            if i > after and e.subject_ref in subjects and (not operations or e.operation in operations)
        ]
        selected = rows[:limit]
        return ObservationPage(
            items=[e for _, e in selected],
            next_cursor=selected[-1][0] if selected else after,
            has_more=len(rows) > limit,
        )


class Answer(ContextPayload):
    text: str


def receive(trace, value, *, call="c", seq=2):
    trace.event({"type": "tool/call", "data": {"callId": call, "name": "read", "arguments": "{}"}})
    trace.event(
        {
            "type": "tool/result",
            "seq": seq,
            "surfaceOp": "append",
            "data": {
                "message": {
                    "role": "tool",
                    "toolCallId": call,
                    "content": [{"type": "text", "text": json.dumps(value)}],
                }
            },
        }
    )


def prepare(monkeypatch):
    sink = Sink()
    trace = DshExecutionTrace(sink, "task", "writer", "session", "offline")
    for key, value in trace.environment().items():
        monkeypatch.setenv(key, value)
    trace.event({"type": "request/header", "data": {"header": {"tools": []}}})
    trace.event({"type": "step/start", "data": {"turn": 1, "step": 1}})

    def read() -> Answer:
        return Answer(text="exact visible payload")

    wrapped = observed_tool(read, sink, "task", "writer", context_tracking=True)
    value = asyncio.run(wrapped()).model_dump(mode="json")
    return sink, trace, wrapped, value


def test_receipt_links_only_matching_sdk_result_and_retained_surface(monkeypatch):
    sink, trace, _, value = prepare(monkeypatch)
    receive(trace, value)
    trace.event({"type": "step/start", "data": {"turn": 1, "step": 2}})
    manifests = [
        ContextManifest.model_validate_json(e.output_json)
        for e in sink.events
        if e.operation == "context_manifest"
    ]
    receipt = value["context_receipt"]["receipt_id"]
    assert manifests[-1].receipt_refs == [receipt]
    assert any(e.operation == "context_delivery" for e in sink.events)
    before = len(manifests)
    trace._model_start()
    assert len([e for e in sink.events if e.operation == "context_manifest"]) == before
    trace.event(
        {
            "type": "system/message",
            "seq": 3,
            "surfaceOp": {"op": "replace", "startSeq": 2, "endSeq": 2},
            "data": {"message": {"role": "system", "content": "short summary"}},
        }
    )
    trace.event({"type": "step/start", "data": {"turn": 1, "step": 3}})
    latest = [e for e in sink.events if e.operation == "context_manifest"][-1]
    assert ContextManifest.model_validate_json(latest.output_json).receipt_refs == []


@pytest.mark.parametrize("tamper", ["payload", "session", "attempt", "unknown", "assistant"])
def test_forged_stale_or_wrong_channel_receipt_does_not_link(monkeypatch, tamper):
    sink, trace, _, value = prepare(monkeypatch)
    if tamper == "payload":
        value["text"] = "different"
    elif tamper == "session":
        trace.identity = trace.identity.model_copy(update={"session_id": "other"})
    elif tamper == "attempt":
        trace.identity = trace.identity.model_copy(update={"attempt": 2})
    elif tamper == "unknown":
        value["context_receipt"]["receipt_id"] = "not-emitted"
    if tamper == "assistant":
        trace.event(
            {
                "type": "assistant/message",
                "seq": 2,
                "surfaceOp": "append",
                "data": {"message": {"role": "assistant", "content": json.dumps(value)}},
            }
        )
    else:
        receive(trace, value)
    assert not any(e.operation == "context_delivery" for e in sink.events)


def test_same_name_calls_keep_distinct_receipts(monkeypatch):
    sink, trace, wrapped, first = prepare(monkeypatch)
    second = asyncio.run(wrapped()).model_dump(mode="json")
    assert first["context_receipt"]["receipt_id"] != second["context_receipt"]["receipt_id"]
    receive(trace, second, call="second", seq=2)
    receive(trace, first, call="first", seq=3)
    links = [json.loads(e.output_json) for e in sink.events if e.operation == "context_delivery"]
    assert [(e["sdk_call_id"], e["receipt_id"]) for e in links] == [
        ("second", second["context_receipt"]["receipt_id"]),
        ("first", first["context_receipt"]["receipt_id"]),
    ]


def test_summary_pages_past_1000_and_filters_attempts():
    sink = Sink()
    task = ProductionTask(
        id="task",
        role="writer",
        actor_id="w",
        model="offline",
        endpoint="",
        prompt_hash="",
        prompt="",
        input_json="{}",
        limits=ProductionLimits(),
        created_at="now",
        updated_at="now",
        attempt=2,
    )
    for index in range(1101):
        manifest = context_manifest(
            {"text": "same"}, str(index), state="host_return_ready", receipt_id=str(index)
        )
        sink.append(
            WorkflowObservation(
                id=str(index),
                subject_ref="task",
                stage="writer",
                operation="context_manifest",
                status="succeeded",
                created_at="now",
                output_json=manifest.model_dump_json(),
                execution=ExecutionIdentity(
                    trace_id="task", span_id=str(index), kind="event", attempt=1 if index < 1100 else 2
                ),
            )
        )
    report = inspect_task_context(task, sink, 1)
    assert report.host_manifests == 1100
    assert next(d.count for d in report.diagnostics if d.code == "CONTEXT_DUPLICATE_PAYLOAD") == 1099
    assert inspect_task_context(task, sink, 2).host_manifests == 1


def test_manifest_size_limit_and_missing_sequences_remain_explicit(monkeypatch):
    manifest = context_manifest({"items": [str(i) for i in range(300)]}, "large", state="host_return_ready")
    assert not manifest.complete and len(manifest.items) == 256
    sink, trace, _, value = prepare(monkeypatch)
    receive(trace, value, seq="missing")
    trace.event({"type": "step/start", "data": {"turn": 1, "step": 2}})
    latest = [e for e in sink.events if e.operation == "context_manifest"][-1]
    assert not ContextManifest.model_validate_json(latest.output_json).complete
