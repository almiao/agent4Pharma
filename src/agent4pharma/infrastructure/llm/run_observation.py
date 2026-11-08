"""保留模型运行的可见结束状态和最终回复，排除隐藏推理与原始供应商诊断。"""

import json
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from agent4pharma.infrastructure.persistence.sqlite.article_regression_repository import ObservationRepository
from agent4pharma.runtime.production_failure import malformed_tool_response
from agent4pharma.runtime.workflow_observation import WorkflowObservation
from agent4pharma.shared.clock import utcnow

if TYPE_CHECKING:
    from deepseek_harness import RunResult


def record_model_result(root: Path, subject: str, stage: str, result: "RunResult") -> None:
    """记录正常最终回复及事件类型统计，不保存 reasoning 流、供应商载荷或凭证。"""
    final = result.final_response[:20000]
    secret = os.environ.get("DEEPSEEK_API_KEY", "")
    if secret:
        final = final.replace(secret, "[REDACTED]")
    diagnostics = []
    for event in result.events:
        if event.get("type") not in {"assistant/attempt", "step/end", "turn/end"}:
            continue
        flags = ["MALFORMED_RESPONSE"] if malformed_tool_response([event]) else []
        data = event.get("data", {})
        data_keys = list(data) if isinstance(data, dict) else []
        error_data = data.get("error", {}) if isinstance(data, dict) else {}
        diagnostics.append(
            {
                "event": event.get("type"),
                "keys": list(event),
                "data_keys": data_keys,
                "error_keys": list(error_data) if isinstance(error_data, dict) else [],
                "error_classes": flags,
            }
        )
    routes = []

    def collect_routes(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str) and item.startswith(("https://", "http://")):
                    parsed = urlsplit(item)
                    routes.append({"key": key, "host": parsed.hostname, "path": parsed.path})
                elif isinstance(item, (dict, list)):
                    collect_routes(item)
        elif isinstance(value, list):
            for item in value:
                collect_routes(item)

    for event in result.events:
        if event.get("type") in {"request/header", "assistant/attempt", "turn/end"}:
            collect_routes(event)
    summary = {
        "finish_reason": result.finish_reason,
        "final_response": final,
        "event_kinds": [
            {key: event[key] for key in ("type", "method", "name") if key in event}
            for event in result.events[:100]
        ],
        "notification_methods": [notice.method for notice in result.notifications[:100]],
    }
    summary["safe_error_classes"] = diagnostics
    summary["request_routes"] = routes
    ObservationRepository(root).append(
        WorkflowObservation(
            id=uuid.uuid4().hex,
            subject_ref=subject,
            stage=stage,
            operation="model_run_result",
            status="succeeded" if result.finish_reason == "completed" else "failed",
            output_json=json.dumps(summary, ensure_ascii=False),
            detail="模型运行已返回；业务是否完成仍以结构化提交校验为准",
            created_at=utcnow(),
        )
    )
    if result.finish_reason != "completed":
        raise RuntimeError("DSH 模型会话未正常完成，见脱敏运行观测")
