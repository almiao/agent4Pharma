"""准确任务的上下文只读诊断；分页消费通用追踪，不触发执行或猜造历史送达。"""

import json
from collections import Counter

from pydantic import BaseModel, Field, ValidationError

from agent4pharma.runtime.context_records import ContextManifest
from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.runtime.workflow_observation import ObservationSink, read_observations


class ContextDiagnostic(BaseModel):
    """可由程序证明的上下文问题，不将模型隐藏推理推断为故障原因。"""

    code: str
    count: int
    detail: str


class ProductionContextReport(BaseModel):
    """人工诊断摘要与模型工作集分型；材料大小不是供应商精确 token。"""

    task_id: str
    attempt: int
    contract: str
    host_manifests: int = 0
    model_calls: int = 0
    model_manifest_revisions: int = 0
    delivered_receipts: int = 0
    max_visible_utf8_bytes: int = 0
    diagnostics: list[ContextDiagnostic] = Field(default_factory=list)
    measurement: str = "SDK公开可见JSON的UTF-8字节，非供应商报文或token"


def inspect_task_context(
    task: ProductionTask, sink: ObservationSink | None, attempt: int
) -> ProductionContextReport:
    """扫描所有分页，严格按尝试过滤；无清单历史明确标未知，不执行重建模型调用。"""
    if attempt < 0 or attempt > task.attempt:
        raise ValueError("上下文尝试号不属于准确任务")
    result = ProductionContextReport(
        task_id=task.id, attempt=attempt, contract=task.context_contract or "legacy"
    )
    if sink is None:
        result.diagnostics.append(
            ContextDiagnostic(code="CONTEXT_MANIFEST_INCOMPLETE", count=1, detail="未配置追踪仓储")
        )
        return result
    hosts: set[str] = set()
    delivered: set[str] = set()
    calls: set[str] = set()
    duplicates: Counter[str] = Counter()
    missing = 0
    latest_complete: dict[str, bool] = {}
    for event in read_observations(sink, [task.id], ("context_manifest", "context_delivery")):
        if not event.execution or event.execution.attempt != attempt:
            continue
        if event.operation == "context_delivery":
            try:
                value = json.loads(event.output_json)
            except ValueError:
                missing += 1
                continue
            if isinstance(value, dict) and value.get("state") == "sdk_result_observed":
                delivered.add(str(value.get("receipt_id", "")))
            continue
        try:
            manifest = ContextManifest.model_validate_json(event.output_json)
        except ValidationError:
            missing += 1
            continue
        if manifest.state == "host_return_ready":
            missing += int(not manifest.complete)
            hosts.add(manifest.receipt_id)
            duplicates[manifest.payload_hash] += 1
        else:
            calls.add(event.execution.parent_span_id)
            latest_complete[event.execution.parent_span_id] = manifest.complete
            result.model_manifest_revisions += 1
            result.max_visible_utf8_bytes = max(
                result.max_visible_utf8_bytes, sum(i.utf8_bytes for i in manifest.items)
            )
    missing += sum(not complete for complete in latest_complete.values())
    result.host_manifests, result.model_calls = len(hosts), len(calls)
    result.delivered_receipts = len(delivered & hosts)
    facts = [
        (
            "CONTEXT_MANIFEST_INCOMPLETE",
            missing or int(not hosts and not calls),
            "缺失或部分清单，不可据此声称完整输入",
        ),
        ("CONTEXT_RECEIPT_UNLINKED", len(hosts - delivered), "宿主准备返回但无SDK接收证据，送达未知"),
        (
            "CONTEXT_DUPLICATE_PAYLOAD",
            sum(n - 1 for n in duplicates.values()),
            "同一业务返回重复取得；可能是显式重读，不自动判故障",
        ),
    ]
    result.diagnostics = [
        ContextDiagnostic(code=code, count=count, detail=detail) for code, count, detail in facts if count
    ]
    return result
