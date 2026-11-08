"""制作失败诊断只使用准确尝试的公开结束状态。"""

from types import SimpleNamespace

import pytest

from agent4pharma.entrypoints.web.production_api import ProductionAPI
from agent4pharma.infrastructure.llm.dsh_execution_trace import DshExecutionTrace
from agent4pharma.infrastructure.llm.production_runner import DshProductionRunner
from agent4pharma.infrastructure.persistence.sqlite.article_regression_repository import ObservationRepository
from agent4pharma.runtime.production_execution import ProductionLimits, ProductionTask
from agent4pharma.runtime.production_failure import (
    ProductionModelFailure,
    malformed_stream,
    model_stream_failure,
    production_failure_detail,
)
from agent4pharma.runtime.workflow_observation import ObservationPage


def test_output_limit_diagnosis_uses_agent_finish_and_exact_attempt():
    events = []
    trace = DshExecutionTrace(SimpleNamespace(append=events.append), "task", "writer", "session", "model")
    trace.begin({})
    trace.finish(SimpleNamespace(finish_reason="max-tokens", final_response=""))

    assert "max-tokens" in production_failure_detail(events, 1)
    assert production_failure_detail(events, 2) == ""
    assert production_failure_detail([events[-1].model_copy(update={"execution": None})], 1) == ""


def test_successful_recovery_supersedes_old_output_limit_and_ignores_text():
    events = []
    trace = DshExecutionTrace(SimpleNamespace(append=events.append), "task", "writer", "session", "model")
    trace.finish(SimpleNamespace(finish_reason="max-tokens", final_response=""))
    trace.finish(SimpleNamespace(finish_reason="completed", final_response="max-tokens"))

    assert production_failure_detail(events, 1) == ""


@pytest.mark.parametrize(
    "code,status,known",
    [("SERVER", 500, True), ("SERVER", 503, False), ("OTHER", 500, False), ("STREAM_CLOSED", None, True)],
)
def test_server_error_requires_explicit_code_and_status(code, status, known):
    data = {
        "stream": [
            {
                "type": "finish",
                "reason": {
                    "failure": {
                        "code": code,
                        "status": status,
                        "message": "private HTTP 500 MALFORMED_RESPONSE",
                    }
                },
            }
        ]
    }
    message = model_stream_failure(data)
    assert bool(message) is known
    assert "private" not in message
    assert not malformed_stream(data)
    events = []
    trace = DshExecutionTrace(SimpleNamespace(append=events.append), "task", "writer", "session", "model")
    trace.event({"type": "assistant/attempt", "data": data})
    trace.finish(SimpleNamespace(finish_reason="error", final_response=""))
    assert production_failure_detail(events, 1) == message
    if known:
        assert ProductionModelFailure(message).public_message == message


def test_worker_and_task_detail_report_output_limit_without_exposing_provider_text(monkeypatch, tmp_path):
    events = []
    trace = DshExecutionTrace(SimpleNamespace(append=events.append), "task", "writer", "session", "model")
    trace.finish(SimpleNamespace(finish_reason="max-tokens", final_response="private provider text"))
    task = ProductionTask(
        id="task",
        role="writer",
        actor_id="writer:task",
        model="model",
        endpoint="https://example.test",
        prompt_hash="hash",
        prompt="write",
        input_json="{}",
        limits=ProductionLimits(),
        attempt=1,
        status="failed",
        error="角色执行失败：RuntimeError",
        created_at="2026-11-07T00:00:00+00:00",
        updated_at="2026-11-07T00:00:00+00:00",
    )

    def read_events(self, subjects, after=0, limit=100, *, operations):
        assert subjects == [task.id]
        return ObservationPage(items=[event for event in events if event.operation in operations])

    monkeypatch.setattr(ObservationRepository, "page", read_events)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-key")
    monkeypatch.setattr(
        "agent4pharma.infrastructure.llm.production_runner.subprocess.Popen",
        lambda *a, **kw: SimpleNamespace(wait=lambda **kw: 1, poll=lambda: 1),
    )
    with pytest.raises(ProductionModelFailure) as caught:
        DshProductionRunner(tmp_path).run(task)
    assert "max-tokens" in caught.value.public_message
    api = ProductionAPI(SimpleNamespace(), lambda _: None, lambda: False, ObservationRepository(tmp_path))
    try:
        assert api._task(task, usage_events=[])["error"] == caught.value.public_message
    finally:
        api.close()
    assert "private" not in caught.value.public_message
    with pytest.raises(ValueError):
        ProductionModelFailure("private provider text")
