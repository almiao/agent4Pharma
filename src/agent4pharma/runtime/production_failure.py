"""制作执行错误的固定公开分类，不通过原始供应商文本猜测 HTTP 状态。"""

import json
from collections.abc import Iterable

from pydantic import JsonValue

from agent4pharma.runtime.workflow_observation import WorkflowObservation

MALFORMED_MESSAGE = "模型交稿或工具参数不是合法JSON（MALFORMED_RESPONSE），未执行该工具；可重试当前阶段。"
OUTPUT_LIMIT_MESSAGE = (
    "模型输出达到单次上限（max-tokens），本次会话未正常完成；可从已提交保存点重试当前阶段。"
)
SERVER_ERROR_MESSAGE = (
    "模型服务返回内部错误（HTTP 500），本次会话未正常完成；已提交产物保留，请稍后重试当前阶段。"
)
STREAM_CLOSED_MESSAGE = (
    "模型响应流中断（STREAM_CLOSED），本次会话未正常完成；已提交产物保留，可稍后重试当前阶段。"
)
CUMULATIVE_LIMIT_MESSAGE = "返修累计执行预算已达到；已保存候选保留，请检查进展和成本后显式继续，未自动重试。"
TOOL_MOUNT_MESSAGE = "任务工具未完整挂载，已在下一次模型调用前停止；请检查MCP启动与冻结工具清单。"


class ProductionModelFailure(RuntimeError):
    """仅承载宿主已分类的固定提示，不接受供应商原始消息。"""

    public_message = MALFORMED_MESSAGE

    def __init__(self, public_message: str = MALFORMED_MESSAGE) -> None:
        """仅接受固定分类提示，禁止把供应商原始错误写入任务。"""
        if public_message not in {
            MALFORMED_MESSAGE,
            OUTPUT_LIMIT_MESSAGE,
            SERVER_ERROR_MESSAGE,
            STREAM_CLOSED_MESSAGE,
            CUMULATIVE_LIMIT_MESSAGE,
            TOOL_MOUNT_MESSAGE,
        }:
            raise ValueError("未知的制作失败分类")
        self.public_message = public_message
        super().__init__(public_message)


def malformed_tool_response(events: Iterable[dict[str, JsonValue]]) -> bool:
    """仅识别供应商明确的 finish.failure.code，不匹配正文或推理中的关键词。"""
    for event in reversed(list(events)):
        data = event.get("data")
        if event.get("type") not in {"assistant/attempt", "assistant/message"} or not isinstance(data, dict):
            continue
        return malformed_stream(data)
    return False


def malformed_stream(data: dict[str, JsonValue]) -> bool:
    """检查已过滤或原始流中的公开完成状态，不读取隐藏推理。"""
    return model_stream_failure(data) == MALFORMED_MESSAGE


def model_stream_failure(data: dict[str, JsonValue]) -> str:
    """仅将明确的结构化失败代码映射为固定诊断，不展示原始错误消息。"""
    stream = data.get("stream")
    for part in stream if isinstance(stream, list) else []:
        if not isinstance(part, dict):
            continue
        chunk = part.get("chunk", part)
        if not isinstance(chunk, dict) or chunk.get("type") != "finish":
            continue
        reason = chunk.get("reason")
        failure = reason.get("failure") if isinstance(reason, dict) else None
        if not isinstance(failure, dict):
            continue
        if failure.get("code") == "MALFORMED_RESPONSE":
            return MALFORMED_MESSAGE
        if failure.get("code") == "SERVER" and failure.get("status") == 500:
            return SERVER_ERROR_MESSAGE
        if failure.get("code") == "STREAM_CLOSED":
            return STREAM_CLOSED_MESSAGE
    return ""


def production_failure_detail(events: Iterable[WorkflowObservation], attempt: int) -> str:
    """从准确尝试的已记录模型失败补充页面诊断，不改写历史任务状态。"""
    for event in reversed(list(events)):
        if event.operation not in {"model_call", "agent_run"} or event.status not in {"failed", "succeeded"}:
            continue
        if event.execution is None or event.execution.attempt != attempt:
            continue
        if event.status != "failed":
            return ""
        data = json.loads(event.output_json or "{}")
        if event.operation == "agent_run":
            if isinstance(data, dict) and data.get("finish_reason") == "max-tokens":
                return OUTPUT_LIMIT_MESSAGE
            # 其他会话失败仍可由最后一次模型调用提供具体诊断。
            continue
        return model_stream_failure(data) if isinstance(data, dict) else ""
    return ""
