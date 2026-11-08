"""将 SDK 实际工具结果与已持久化宿主回执配对；不按时间、名称或自由文本猜测。"""

import json

from pydantic import JsonValue, ValidationError

from agent4pharma.runtime.context_records import ContextManifest, ContextReceipt, payload_hash
from agent4pharma.runtime.workflow_observation import ObservationSink, read_observations


def delivered_receipt(
    sink: ObservationSink, subject: str, session: str, attempt: int, message: dict[str, JsonValue]
) -> str:
    """只接受 SDK tool/result 的结构化业务结果及本会话匹配 hash 的宿主回执。"""
    candidates = [message.get("structuredContent")]
    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                try:
                    candidates.append(json.loads(block["text"]))
                except ValueError:
                    continue
    for value in candidates:
        if not isinstance(value, dict) or not isinstance(value.get("context_receipt"), dict):
            continue
        try:
            receipt = ContextReceipt.model_validate(value["context_receipt"])
        except ValidationError:
            continue
        if receipt.result_hash != payload_hash(value):
            continue
        for event in read_observations(sink, [subject], ("context_manifest",)):
            identity = event.execution
            if not identity or (identity.session_id, identity.attempt) != (session, attempt):
                continue
            try:
                manifest = ContextManifest.model_validate_json(event.output_json)
            except ValidationError:
                continue
            if (manifest.state, manifest.manifest_id, manifest.receipt_id, manifest.payload_hash) == (
                "host_return_ready",
                receipt.manifest_id,
                receipt.receipt_id,
                receipt.result_hash,
            ):
                return receipt.receipt_id
    return ""
