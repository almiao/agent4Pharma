"""已推荐论文完整重做：独立制作根任务采用当前配置，原任务与产物保持原样。"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from agent4pharma.runtime.production_execution import ProductionLimits, ProductionTask
from agent4pharma.shared.clock import utcnow
from agent4pharma.workflows.production_dependencies import ProductionContext
from agent4pharma.workflows.production_queue import queue_role

if TYPE_CHECKING:
    from agent4pharma.workflows.production_workbench import ProductionWorkbench


class RemakeProduction(BaseModel):
    """人工确认已推荐论文的新制作轮次；请求身份用于重复提交幂等，工作台将自动执行新轮次。"""

    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(pattern=r"^human-paper-[a-f0-9]{32}$")
    request_id: UUID
    max_revision_rounds: int = Field(default=5, ge=1, le=10)


class ProductionRemakeSummary(BaseModel):
    """重做轮次的准确根任务引用，不携带旧稿件正文或模型提示词。"""

    task_id: str
    source_task_id: str
    status: str
    created_at: str


def latest_candidate_tasks(tasks: list[ProductionTask]) -> dict[str, ProductionTask]:
    """按冻结输入中的准确推荐身份查找已有制作，包含无完整制作链的历史任务。"""
    latest: dict[str, ProductionTask] = {}
    for task in tasks:
        for candidate in ProductionContext.model_validate_json(task.input_json).candidates:
            previous = latest.get(candidate.id)
            if previous is None or task.created_at > previous.created_at:
                latest[candidate.id] = task
    return latest


def paper_remakes(app: ProductionWorkbench, candidate_id: str) -> list[ProductionRemakeSummary]:
    """返回准确论文最近十轮重做，仅查询，不启动或恢复任务。"""
    with app.unit() as work:
        result = []
        for task in work.tasks.list():
            context = ProductionContext.model_validate_json(task.input_json)
            if (
                not task.parent_task_id
                and context.remake_request_id
                and any(candidate.id == candidate_id for candidate in context.candidates)
            ):
                result.append(
                    ProductionRemakeSummary(
                        task_id=task.id,
                        source_task_id=context.remake_of_task_id,
                        status=task.status,
                        created_at=task.created_at,
                    )
                )
        return sorted(result, key=lambda row: row.created_at, reverse=True)[:10]


def remake_paper(app: ProductionWorkbench, request: RemakeProduction) -> ProductionTask:
    """校验原制作与有效推荐后排队新根任务；不复用旧稿/任务书，不执行模型。

    来源主文通过候选公开端口重新校验，在生产事务外读取。生产根任务及操作记录在同一工作单元提交；
    相同请求返回已排队任务，即使随后发布提示词也不重复创建或替换其快照。
    """
    identity = str(request.request_id)
    requested_rounds = (
        request.max_revision_rounds if "max_revision_rounds" in request.model_fields_set else None
    )
    with app.unit() as work:
        existing = _existing_remake(work.tasks.list(), request.candidate_id, identity, requested_rounds)
        if existing is not None:
            return existing
    candidates = app.candidates([request.candidate_id])
    if len(candidates) != 1 or candidates[0].id != request.candidate_id:
        raise ValueError("人工推荐已失效，请重新核对论文和主文")
    if not app.definitions["writer"].newsroom_pipeline:
        raise ValueError("完整重做需要启用采编制作流程")
    with app.unit() as work:
        tasks = work.tasks.list()
        existing = _existing_remake(tasks, request.candidate_id, identity, requested_rounds)
        if existing is not None:
            return existing
        source = latest_candidate_tasks(tasks).get(request.candidate_id)
        if source is None:
            raise ValueError("该论文尚未开始制作，请先启动制作")
        task = queue_role(
            work,
            app.definitions,
            "editor",
            ProductionContext(
                candidates=candidates,
                phase="topic-selection",
                remake_request_id=identity,
                remake_of_task_id=source.id,
            ),
            ProductionLimits.for_new_task().model_copy(
                update={"max_revision_rounds": request.max_revision_rounds}
            ),
        )
        work.tasks.event(task.id + ":remake", "", "operator", "remake", f"{source.id} -> {task.id}", utcnow())
        return task


def _existing_remake(
    tasks: list[ProductionTask], candidate_id: str, request_id: str, requested_rounds: int | None
) -> ProductionTask | None:
    for task in tasks:
        context = ProductionContext.model_validate_json(task.input_json)
        if context.remake_request_id != request_id:
            continue
        if not any(candidate.id == candidate_id for candidate in context.candidates):
            raise ValueError("重做请求已绑定其他论文")
        if not task.parent_task_id:
            if requested_rounds is not None and task.limits.max_revision_rounds != requested_rounds:
                raise ValueError("此重做请求已冻结其他返修额度，不能覆盖；请查看已有轮次或另行发起新请求")
            return task
    return None
