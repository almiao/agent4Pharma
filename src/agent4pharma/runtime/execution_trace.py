"""与业务无关的执行关联与载荷过滤；Agent、模型、工具和MCP共用同一契约。"""

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ExecutionIdentity(BaseModel):
    """一个实际执行单元的身份；同一span的开始和结束共用身份，不按名称猜关联。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    trace_id: str
    span_id: str
    parent_span_id: str = ""
    kind: Literal["workflow", "agent", "model", "tool", "mcp", "event"]
    agent_id: str = ""
    framework: str = ""
    provider: str = ""
    model: str = ""
    session_id: str = ""
    attempt: int = Field(default=0, ge=0)
    turn: int | None = None
    step: int | None = None
    call_id: str = ""
    server: str = ""
    duration_ms: float | None = Field(default=None, ge=0)
    input_scope: str = ""
    source: str = ""


def agent_span_id(subject: str, attempt: int) -> str:
    """按准确主体和尝试标识Agent调用，不把业务角色名当作执行身份。"""
    return f"agent:{subject}:{attempt}"


_SENSITIVE = re.compile(
    r"^(authorization|proxy.authorization|(?:x[-_])?api.?key|access.?token|refresh.?token|token|password|secret|client.?secret|"
    r"lease.?token|cookie|set.cookie|credential[s]?)$",
    re.I,
)


def safe_trace_value(value: JsonValue, secrets: tuple[str, ...] = ()) -> JsonValue:
    """递归过滤凭证、隐藏推理和二进制；未知业务参数保留原键，不执行载荷内容。"""
    if isinstance(value, dict):
        if str(value.get("type", "")).startswith(("reasoning", "thinking")):
            return {"type": str(value["type"]), "omitted": True}
        if value.get("type") == "error":
            return {"type": "error", "omitted": True}
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            binary = isinstance(item, str) and (
                key == "data"
                and value.get("type") in {"image", "audio"}
                or key == "blob"
                and "mimeType" in value
            )
            if _SENSITIVE.match(key):
                result[key] = "[REDACTED]"
            elif binary:
                result[key] = {"omitted": True, "characters": len(item)}
            elif key in {"reasoning", "reasoning_content", "hidden_reasoning"}:
                result[key] = "[OMITTED]"
            else:
                result[key] = safe_trace_value(item, secrets)
        return result
    if isinstance(value, list):
        return [safe_trace_value(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
    return value


def trace_json(value: JsonValue, secrets: tuple[str, ...] = ()) -> str:
    """序列化安全JSON；持久化边界另行实施载荷上限与截断标记。"""
    return json.dumps(safe_trace_value(value, secrets), ensure_ascii=False)
