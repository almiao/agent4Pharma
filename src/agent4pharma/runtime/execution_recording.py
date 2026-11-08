"""通用执行边界的观测写入；不决定任何业务任务的完成或重试。"""

import uuid
from typing import Literal

from pydantic import JsonValue

from agent4pharma.runtime.execution_trace import ExecutionIdentity, trace_json
from agent4pharma.runtime.workflow_observation import (
    ObservationSink,
    WorkflowObservation,
    bounded_observation_json,
)
from agent4pharma.shared.clock import utcnow


class ExecutionRecorder:
    """统一追加Agent/模型/工具事件，过滤后才进入持久化端口。"""

    def __init__(
        self, sink: ObservationSink, subject: str, stage: str, secrets: tuple[str, ...] = ()
    ) -> None:
        self.sink, self.subject, self.stage, self.secrets = sink, subject, stage, secrets

    def record(
        self,
        identity: ExecutionIdentity,
        operation: str,
        status: Literal["started", "succeeded", "failed", "blocked", "skipped"],
        *,
        inputs: JsonValue = None,
        outputs: JsonValue = None,
        detail: str = "",
        event_id: str = "",
    ) -> None:
        """按调用者提供的准确身份追加事件；未知输入与空对象分别保留。"""
        self.sink.append(
            WorkflowObservation(
                id=event_id or uuid.uuid4().hex,
                subject_ref=self.subject,
                stage=self.stage,
                operation=operation,
                status=status,
                execution=identity,
                input_json=bounded_observation_json(trace_json(inputs, self.secrets))
                if inputs is not None
                else "",
                output_json=bounded_observation_json(trace_json(outputs, self.secrets))
                if outputs is not None
                else "",
                detail=stripped_detail(detail, self.secrets),
                created_at=utcnow(),
            )
        )


def stripped_detail(detail: str, secrets: tuple[str, ...]) -> str:
    """错误说明与输入输出使用相同脱敏规则，并保留字段长度上限。"""
    from agent4pharma.runtime.execution_trace import safe_trace_value

    return str(safe_trace_value(detail, secrets))[:4000]
