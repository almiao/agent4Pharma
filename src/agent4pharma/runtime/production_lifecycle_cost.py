"""按真实任务父子链汇总全周期成本、失败与审核终点；不按标题猜配，不隐去重试。"""

import hashlib
from collections.abc import Mapping, Sequence
from decimal import Decimal

from pydantic import BaseModel

from agent4pharma.runtime.production_cost import ProductionCost, estimate_production_cost
from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.runtime.production_usage import ProductionUsageView, summarize_usage
from agent4pharma.runtime.workflow_observation import WorkflowObservation


class LifecycleTaskCost(BaseModel):
    """一个真实任务的全部尝试，金额是已上报用量估算而非供应商账单。"""

    task_id: str
    parent_task_id: str
    phase: str
    status: str
    result_ref: str
    usage: ProductionUsageView
    cost: ProductionCost
    failure_fingerprint: str = ""


class LifecycleCost(BaseModel):
    """同一根任务的累计已发生成本和未完成边界，不把单轮交稿当作最终质量通过。"""

    root_task_id: str
    tasks: list[LifecycleTaskCost]
    requests: int
    reported_total_tokens: int | None
    estimated_cny: str | None
    partial: bool
    pending_task_ids: list[str]
    failed_task_ids: list[str]
    note: str = "包含所选根任务及真实后继全部尝试/压缩；不含未知用量及人工成本，待审不等于通过。"


def lifecycle_task_ids(tasks: Sequence[ProductionTask], root_id: str) -> set[str]:
    """仅沿明确parent_task_id向下展开；缺根时拒绝产生看似零成本的结果。"""
    if root_id not in {t.id for t in tasks}:
        raise KeyError(root_id)
    selected = {root_id}
    while True:
        expanded = selected | {t.id for t in tasks if t.parent_task_id in selected}
        if expanded == selected:
            return selected
        selected = expanded


def summarize_lifecycle_cost(
    tasks: Sequence[ProductionTask], events: Mapping[str, Sequence[WorkflowObservation]], root_id: str
) -> LifecycleCost:
    """读取者负责取得完整分页事件；不触发执行、不合并不同重做根任务。"""
    ids = lifecycle_task_ids(tasks, root_id)
    rows = []
    for task in tasks:
        if task.id not in ids:
            continue
        observations = events.get(task.id, ())
        fingerprint = hashlib.sha256(f"{task.phase}|{task.model}|{task.error}".encode()).hexdigest()[:20]
        rows.append(
            LifecycleTaskCost(
                task_id=task.id,
                parent_task_id=task.parent_task_id,
                phase=task.phase,
                status=task.status,
                result_ref=task.result_ref,
                usage=summarize_usage(observations),
                cost=estimate_production_cost(observations, task.model, task.endpoint),
                failure_fingerprint=fingerprint if task.error else "",
            )
        )
    attempted = [r for r in rows if r.usage.attempts or r.status not in {"queued", "cancelled"}]
    totals = [r.usage.reported_total_tokens for r in attempted]
    amounts = [Decimal(r.cost.amount) for r in attempted if r.cost.amount is not None]
    pending = [r.task_id for r in rows if r.status in {"queued", "running", "paused", "blocked"}]
    return LifecycleCost(
        root_task_id=root_id,
        tasks=rows,
        requests=sum(a.requests for r in rows for a in r.usage.attempts),
        reported_total_tokens=sum(totals) if totals and all(n is not None for n in totals) else None,
        estimated_cny=str(sum(amounts)) if amounts else None,
        partial=bool(pending) or any(r.cost.partial for r in attempted),
        pending_task_ids=pending,
        failed_task_ids=[r.task_id for r in rows if r.status == "failed"],
    )
