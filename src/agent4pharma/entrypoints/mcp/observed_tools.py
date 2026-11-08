"""记录 MCP 工具调用的业务参数、结果与校验错误，不记录模型隐藏推理。"""

import json
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from functools import wraps
from inspect import isawaitable, signature

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult
from pydantic import BaseModel

from agent4pharma.runtime.context_records import ContextPayload, ContextReceipt, context_manifest
from agent4pharma.runtime.execution_trace import ExecutionIdentity, agent_span_id, trace_json
from agent4pharma.runtime.workflow_observation import (
    ObservationSink,
    WorkflowObservation,
    bounded_observation_json,
)
from agent4pharma.shared.clock import utcnow


def observed_tool[**P, R](
    method: Callable[P, R],
    sink: ObservationSink,
    subject_ref: str,
    stage: str,
    *,
    attempt: int = 1,
    server: str = "pharma",
    operation: str | None = None,
    context_tracking: bool = False,
) -> Callable[P, Awaitable[R]]:
    """保留原签名供 MCP 生成 schema；仅包装白名单业务工具，不接触密钥配置。"""

    def serialize(value: object) -> str:
        if isinstance(value, CallToolResult):
            value = value.model_dump(mode="json", by_alias=True)

        def fallback(item: object) -> object:
            return item.model_dump(mode="json") if isinstance(item, BaseModel) else str(type(item).__name__)

        encoded = json.loads(json.dumps(value, ensure_ascii=False, default=fallback))
        return bounded_observation_json(trace_json(encoded, (os.environ.get("DEEPSEEK_API_KEY", ""),)))

    @wraps(method)
    async def invoke(*args: P.args, **kwargs: P.kwargs) -> R:
        bound = signature(method).bind(*args, **kwargs)
        inputs = serialize(dict(bound.arguments))
        identity = uuid.uuid4().hex
        started = time.monotonic()
        execution = ExecutionIdentity(
            trace_id=subject_ref,
            span_id="mcp:" + identity,
            parent_span_id=agent_span_id(subject_ref, attempt),
            kind="mcp",
            agent_id=stage,
            attempt=attempt,
            call_id=identity,
            server=server,
            framework="mcp",
            input_scope="MCP宿主参数与返回；未采集SDK上游callId，不猜测对应模型调用",
            source="mcp.host",
        )
        if os.environ.get("PHARMA_EXECUTION_SUBJECT") == subject_ref:
            execution = execution.model_copy(
                update={
                    "trace_id": os.environ.get("PHARMA_EXECUTION_TRACE_ID") or subject_ref,
                    "parent_span_id": os.environ.get("PHARMA_EXECUTION_AGENT_SPAN_ID")
                    or execution.parent_span_id,
                    "session_id": os.environ.get("PHARMA_EXECUTION_SESSION_ID", ""),
                }
            )
        sink.append(
            WorkflowObservation(
                id=identity + "-start",
                subject_ref=subject_ref,
                stage=stage,
                operation=operation or method.__name__,
                status="started",
                input_json=inputs,
                created_at=utcnow(),
                execution=execution,
            )
        )
        try:
            result = method(*args, **kwargs)
            if isawaitable(result):
                result = await result
        except Exception as exc:
            detail = str(exc)[:1000] if isinstance(exc, (ValueError, KeyError)) else type(exc).__name__
            secret = os.environ.get("DEEPSEEK_API_KEY", "")
            if secret:
                detail = detail.replace(secret, "[REDACTED]")
            sink.append(
                WorkflowObservation(
                    id=identity + "-end",
                    subject_ref=subject_ref,
                    stage=stage,
                    operation=operation or method.__name__,
                    status="failed",
                    input_json=inputs,
                    detail=detail,
                    created_at=utcnow(),
                    execution=execution.model_copy(
                        update={"duration_ms": (time.monotonic() - started) * 1000}
                    ),
                )
            )
            if isinstance(exc, (ValueError, KeyError)):
                raise ToolError(detail) from exc
            raise ToolError(
                f"宿主工具执行异常（{type(exc).__name__}），结果未确认。"
                f"请勿反复修改参数重试，报告阻断并保留诊断编号：{identity}"
            ) from exc
        if context_tracking and isinstance(result, ContextPayload):
            # 只读工作集在业务事务关闭后登记，写入失败不宣称已送达。
            payload = result.model_dump(mode="json", exclude={"context_receipt"})
            manifest = context_manifest(
                payload,
                "context:" + identity,
                state="host_return_ready",
                receipt_id=identity,
                inclusion_reason=operation or method.__name__,
            )
            sink.append(
                WorkflowObservation(
                    id=identity + "-context",
                    subject_ref=subject_ref,
                    stage=stage,
                    operation="context_manifest",
                    status="succeeded",
                    created_at=utcnow(),
                    execution=execution.model_copy(
                        update={"span_id": "context:" + identity, "kind": "event"}
                    ),
                    output_json=manifest.model_dump_json(),
                )
            )
            result = result.model_copy(
                update={
                    "context_receipt": ContextReceipt(
                        receipt_id=identity,
                        manifest_id=manifest.manifest_id,
                        result_hash=manifest.payload_hash,
                    )
                }
            )
        sink.append(
            WorkflowObservation(
                id=identity + "-end",
                subject_ref=subject_ref,
                stage=stage,
                operation=operation or method.__name__,
                status="failed" if isinstance(result, CallToolResult) and result.is_error else "succeeded",
                input_json=inputs,
                output_json=serialize(result),
                created_at=utcnow(),
                execution=execution.model_copy(update={"duration_ms": (time.monotonic() - started) * 1000}),
            )
        )
        return result

    return invoke
