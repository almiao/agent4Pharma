"""锁定DSH会话事件适配到通用执行追踪；SDK可见上下文不冒充供应商HTTP报文。"""

import json
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from pydantic import JsonValue

from agent4pharma.infrastructure.llm.context_delivery_trace import delivered_receipt
from agent4pharma.runtime.context_records import context_manifest, payload_hash
from agent4pharma.runtime.execution_recording import ExecutionRecorder
from agent4pharma.runtime.execution_trace import ExecutionIdentity, safe_trace_value
from agent4pharma.runtime.production_failure import malformed_tool_response
from agent4pharma.runtime.workflow_observation import ObservationSink

if TYPE_CHECKING:
    from deepseek_harness import DeepSeekHarness, RunResult
    from deepseek_harness.models import Notification


class DshExecutionTrace:
    """按session/turn/step/callId关联记录，未知类型不猜内容或因果关系。"""

    def __init__(
        self,
        sink: ObservationSink,
        subject: str,
        agent: str,
        session: str,
        model: str,
        attempt: int = 1,
        secrets: tuple[str, ...] = (),
    ) -> None:
        self.recorder = ExecutionRecorder(sink, subject, agent, secrets)
        self.identity = ExecutionIdentity(
            trace_id=subject,
            span_id=f"agent:{session}",
            kind="agent",
            agent_id=agent,
            framework="deepseek-harness",
            provider="deepseek-official",
            model=model,
            session_id=session,
            attempt=attempt,
            source="host",
        )
        self.header: JsonValue = None
        self.route: dict[str, str] = {}
        self.surface: list[tuple[int, JsonValue]] = []
        self.surface_complete = True
        self.turn, self.step, self.retry = 0, 0, 0
        self.started: dict[str, float] = {}
        self.calls: dict[str, ExecutionIdentity] = {}
        self.manifests: set[tuple[str, str]] = set()
        self.receipts: dict[str, str] = {}
        self.receipt_sequences: dict[int, str] = {}

    def environment(self) -> dict[str, str]:
        """将非凭证关联信息传给当前SDK及其MCP子进程，不改宿主全局环境。"""
        return {
            "PHARMA_EXECUTION_SUBJECT": self.recorder.subject,
            "PHARMA_EXECUTION_TRACE_ID": self.identity.trace_id,
            "PHARMA_EXECUTION_AGENT_SPAN_ID": self.identity.span_id,
            "PHARMA_EXECUTION_SESSION_ID": self.identity.session_id,
        }

    def run(
        self,
        harness: "DeepSeekHarness",
        instruction: str,
        *,
        bound_input: JsonValue = None,
        on_notification: Callable[["Notification"], None] | None = None,
        repair_malformed: Callable[[], bool] | None = None,
    ) -> "RunResult":
        """通用调用边界；制作可在原租约硬时限内允许一次非法JSON纠正。"""
        self.begin({"instruction": instruction, "bound_input": bound_input})

        def observe(notification: "Notification") -> None:
            self.observe(notification)
            if on_notification is not None:
                on_notification(notification)

        try:
            result = harness.run(instruction, session_id=self.identity.session_id, on_notification=observe)
            if (
                repair_malformed
                and result.finish_reason != "completed"
                and malformed_tool_response(result.events)
                and repair_malformed()
            ):
                correction = (
                    "上次工具调用未执行：参数不是合法JSON。只纠正参数后重提，禁止代码表达式、"
                    ".replace()、拼接或省略号；ID与哈希完整复制。submit_draft.claims.kind只允许"
                    "fact/attributed_claim/inference，不能沿用研究底稿的number/comparison分类。"
                    "复用已保存底稿和引用，不重新研究；使用当前任务工具列表中的恢复入口核对保存点。"
                )
                self.recorder.record(
                    self.identity, "malformed_response_recovery", "started", inputs=correction
                )
                result = harness.run(correction, session_id=self.identity.session_id, on_notification=observe)
            self.finish(result)
            return result
        except Exception as exc:
            self.fail(type(exc).__name__)
            raise

    def begin(self, inputs: JsonValue) -> None:
        """记录实际交给Agent的启动输入及绑定配置，不将绑定资料称为模型已读取。"""
        self.started[self.identity.span_id] = time.monotonic()
        self.recorder.record(self.identity, "agent_run", "started", inputs=inputs)

    def finish(self, result: "RunResult") -> None:
        """保留公开回复；Agent会话结束与业务产物提交是不同状态。"""
        status = "succeeded" if result.finish_reason == "completed" else "failed"
        self.recorder.record(
            self._ended(self.identity),
            "agent_run",
            status,
            outputs={
                "finish_reason": result.finish_reason,
                "final_response": result.final_response,
            },
        )

    def fail(self, error_type: str) -> None:
        """异常边界仅记类型，不把供应商异常文本或凭证带入观测。"""
        self.recorder.record(
            self._ended(self.identity), "agent_run", "failed", outputs={"error_type": error_type}
        )

    def observe(self, notification: "Notification") -> None:
        """仅消费当前已授权会话，SDK广播的其他会话不进入当前记录。"""
        if notification.method != "session.event":
            return
        if notification.payload.get("sessionId") != self.identity.session_id:
            return
        event = notification.payload.get("event")
        if isinstance(event, dict):
            self.event(event)

    def event(self, event: dict[str, JsonValue]) -> None:
        """处理已核对的rc.2事件；公开消息和工具结果维护可见上下文。"""
        kind, data = event.get("type"), event.get("data")
        if not isinstance(data, dict):
            return
        if kind == "step/start":
            self.turn, self.step = int(data.get("turn", 0)), int(data.get("step", 0))
            self.retry = 0
            self._model_start()
        elif kind == "request/header":
            self.header = safe_trace_value(data.get("header"), self.recorder.secrets)
            self._model_start()
        elif kind == "request/context":
            self.route = {
                key: value
                for key, value in data.items()
                if key in {"provider", "model"} and isinstance(value, str)
            }
            self._model_start()
        elif kind in {"assistant/message", "assistant/attempt"}:
            self._model_start()
            identity = self._model_identity()
            message = data.get("message") or {"stream": data.get("stream", [])}
            self.recorder.record(
                self._ended(identity),
                "model_call",
                "failed" if kind == "assistant/attempt" else "succeeded",
                outputs=message,
            )
            if kind == "assistant/attempt":
                self.retry += 1
        elif kind == "tool/call":
            self._tool_start(data)
        elif kind == "tool/result":
            self._tool_end(data)
        if kind in {
            "system/message",
            "developer/message",
            "user/message",
            "assistant/message",
            "tool/result",
        }:
            self._surface_event(event, data)

    def _model_identity(self) -> ExecutionIdentity:
        return self.identity.model_copy(
            update={
                "span_id": f"model:{self.identity.session_id}:{self.turn}:{self.step}:{self.retry}",
                "parent_span_id": self.identity.span_id,
                "kind": "model",
                "turn": self.turn,
                "step": self.step,
                "call_id": f"{self.turn}:{self.step}:{self.retry}",
                "source": "dsh.session.event",
                "provider": self.route.get("provider", self.identity.provider),
                "model": self.route.get("model", self.identity.model),
                "input_scope": "SDK可见上下文；非供应商HTTP报文；推理及二进制已省略",
            }
        )

    def _model_start(self) -> None:
        if not self.step:
            return
        identity = self._model_identity()
        self.started.setdefault(identity.span_id, time.monotonic())
        payload = {
            "request_header": self.header,
            "messages": [message for _, message in self.surface],
            "context_complete": self.surface_complete and self.header is not None,
        }
        digest = payload_hash(payload)
        if (identity.span_id, digest) not in self.manifests:
            manifest = context_manifest(
                payload,
                identity.span_id + ":" + digest,
                state="included_in_model_context",
                complete=self.surface_complete and self.header is not None,
                receipt_refs=sorted(
                    {self.receipt_sequences[seq] for seq, _ in self.surface if seq in self.receipt_sequences}
                ),
            )
            self.recorder.record(
                identity.model_copy(
                    update={
                        "kind": "event",
                        "span_id": manifest.manifest_id,
                        "parent_span_id": identity.span_id,
                    }
                ),
                "context_manifest",
                "succeeded",
                outputs=manifest.model_dump(mode="json"),
            )
            self.manifests.add((identity.span_id, digest))
        self.recorder.record(
            identity,
            "model_call",
            "started",
            inputs={
                "request_header": self.header,
                "messages": [message for _, message in self.surface],
                "context_complete": self.surface_complete and self.header is not None,
            },
        )

    def _surface_event(self, event: dict[str, JsonValue], data: dict[str, JsonValue]) -> None:
        seq, operation = event.get("seq"), event.get("surfaceOp")
        if not isinstance(seq, int):
            self.surface_complete = False
            return
        message = safe_trace_value(data.get("message", data), self.recorder.secrets)
        if isinstance(operation, dict) and operation.get("op") == "replace":
            ids = [index for index, _ in self.surface]
            start, end = operation.get("startSeq"), operation.get("endSeq")
            if start not in ids or end not in ids or ids.index(start) > ids.index(end):
                self.surface_complete = False
                return
            self.surface[ids.index(start) : ids.index(end) + 1] = [(seq, message)]
        elif operation == "append":
            self.surface.append((seq, message))
        else:
            self.surface_complete = False
        if event.get("type") == "tool/result" and isinstance(message, dict):
            receipt = self.receipts.get(str(message.get("toolCallId", message.get("callId"))))
            if receipt:
                self.receipt_sequences[seq] = receipt

    def _tool_start(self, data: dict[str, JsonValue]) -> None:
        call_id = data.get("callId")
        if not isinstance(call_id, str) or not call_id:
            return
        identity = self._model_identity().model_copy(
            update={
                "span_id": f"tool:{self.identity.session_id}:{call_id}",
                "kind": "tool",
                "parent_span_id": self._model_identity().span_id,
                "call_id": call_id,
                "input_scope": "SDK实际工具调用参数",
            }
        )
        self.calls[call_id] = identity
        self.started.setdefault(identity.span_id, time.monotonic())
        arguments = data.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                pass
        self.recorder.record(identity, str(data.get("name", "tool_call")), "started", inputs=arguments)

    def _tool_end(self, data: dict[str, JsonValue]) -> None:
        message = data.get("message")
        if not isinstance(message, dict):
            return
        call_id = message.get("toolCallId", message.get("callId"))
        identity = self.calls.get(str(call_id))
        if identity is None:
            return
        if not message.get("isError"):
            receipt = delivered_receipt(
                self.recorder.sink,
                self.recorder.subject,
                self.identity.session_id,
                self.identity.attempt,
                message,
            )
            if receipt:
                self.receipts[str(call_id)] = receipt
                self.recorder.record(
                    identity.model_copy(update={"kind": "event", "span_id": identity.span_id + ":delivery"}),
                    "context_delivery",
                    "succeeded",
                    outputs={
                        "receipt_id": receipt,
                        "state": "sdk_result_observed",
                        "sdk_call_id": str(call_id),
                    },
                )
        # operation只用于名称展示，配对依赖span_id，不依赖工具名或时间。
        self.recorder.record(
            self._ended(identity),
            "tool_result",
            "failed" if message.get("isError") else "succeeded",
            outputs=message,
        )

    def _ended(self, identity: ExecutionIdentity) -> ExecutionIdentity:
        start = self.started.get(identity.span_id)
        return identity.model_copy(
            update={
                "duration_ms": round((time.monotonic() - start) * 1000, 3) if start is not None else None,
            }
        )
