"""通用上下文清单与非凭证回执；只描述可证明的公开载荷，不保存隐藏推理。"""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field, JsonValue


class ContextReceipt(BaseModel):
    """宿主结果的显式关联身份，不授予读取权限。"""

    receipt_id: str
    manifest_id: str
    result_hash: str


class ContextPayload(BaseModel):
    """只读工具可附带回执；未提供时不改变旧返回序列化。"""

    context_receipt: ContextReceipt | None = Field(default=None, exclude_if=lambda value: value is None)


class ContextManifestItem(BaseModel):
    """公开字段或消息的准确 hash、大小及已知来源身份，不重复正文。"""

    payload_path: str
    content_hash: str
    characters: int
    utf8_bytes: int
    artifact_id: str = ""
    artifact_hash: str = ""
    start: int | None = None
    end: int | None = None
    coordinate_system: str = "python-unicode-codepoint"


class ContextManifest(BaseModel):
    """宿主或具体模型调用的不可变清单，不冒充供应商 HTTP 请求。"""

    manifest_id: str
    state: Literal["host_return_ready", "included_in_model_context"]
    payload_hash: str
    items: list[ContextManifestItem]
    complete: bool
    receipt_id: str = ""
    receipt_refs: list[str] = Field(default_factory=list)
    missing_reason: str = ""
    inclusion_reason: str = ""


def canonical_payload(value: JsonValue) -> str:
    """稳定 JSON 序列化，忽略传输空白；顶层回执不参与业务 hash。"""
    if isinstance(value, dict):
        value = {key: item for key, item in value.items() if key != "context_receipt"}
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(value: JsonValue) -> str:
    """规范化 v1 的业务载荷 SHA-256。"""
    return hashlib.sha256(canonical_payload(value).encode()).hexdigest()


def context_manifest(
    value: JsonValue,
    identity: str,
    *,
    state: Literal["host_return_ready", "included_in_model_context"],
    complete: bool = True,
    receipt_id: str = "",
    receipt_refs: list[str] | None = None,
    inclusion_reason: str = "",
) -> ContextManifest:
    """按不重叠顶层字段/列表成员计量；最多256项，超出明确标不完整。"""
    items: list[ContextManifestItem] = []
    fields = value.items() if isinstance(value, dict) else [("$", value)]
    entries = [(key, item) for key, item in fields if key != "context_receipt"]
    flat = [
        (f"{key}/{index}", child)
        for key, item in entries
        if isinstance(item, list)
        for index, child in enumerate(item)
    ]
    flat += [(key, item) for key, item in entries if not isinstance(item, list)]
    for path, item in flat[:256]:
        serialized = canonical_payload(item)
        source = item if isinstance(item, dict) else (value if isinstance(value, dict) else {})
        if isinstance(source.get("item"), dict):
            source = source["item"]
        items.append(
            ContextManifestItem(
                payload_path=path,
                content_hash=payload_hash(item),
                characters=len(serialized),
                utf8_bytes=len(serialized.encode()),
                artifact_id=str(source.get("evidence_id") or source.get("id") or source.get("ref") or ""),
                artifact_hash=str(source.get("content_hash") or ""),
                start=source.get("start") if isinstance(source.get("start"), int) else None,
                end=source.get("end") if isinstance(source.get("end"), int) else None,
            )
        )
    full = complete and len(flat) <= 256
    return ContextManifest(
        manifest_id=identity,
        state=state,
        payload_hash=payload_hash(value),
        items=items,
        complete=full,
        receipt_id=receipt_id,
        receipt_refs=receipt_refs or [],
        missing_reason="" if full else "SDK消息不完整或清单条目超过上限",
        inclusion_reason=inclusion_reason,
    )
