"""制作任务领取、恢复与受限执行；租约令牌防止晚到模型覆盖新结果。"""

import hashlib
import json
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent4pharma.runtime.production_execution import ProductionRunner, ProductionTask
from agent4pharma.runtime.production_failure import ProductionModelFailure
from agent4pharma.shared.clock import utcnow
from agent4pharma.workflows.production_dependencies import ProductionContext, ProductionUnit


class ControlProductionTask(BaseModel):
    """人工控制准确阶段和尝试；工作台继续/重试由调度直接执行。"""

    model_config = ConfigDict(extra="forbid")
    task_id: str
    action: Literal["pause", "stop", "resume", "retry"]
    expected_status: str
    expected_attempt: int = Field(ge=0)


class ProductionExecution:
    """任务状态用例；模型失败不会生成稿件、审核通过或发行成功。"""

    def __init__(
        self,
        unit: Callable[[], AbstractContextManager[ProductionUnit]],
        read_unit: Callable[[], AbstractContextManager[ProductionUnit]] | None = None,
    ) -> None:
        self.unit = unit
        self.read_unit = read_unit or unit

    def get(self, task_id: str) -> ProductionTask:
        """读取准确任务及其绑定快照。"""
        with self.read_unit() as work:
            return work.tasks.get(task_id)

    def is_current(self, original: ProductionTask) -> bool:
        """运行器轮询租约，暂停、停止或业务修订后终止旧子进程。"""
        task = self.get(original.id)
        return task.status not in {"paused", "cancelled"} and task.lease_token == original.lease_token

    def request_automatic(self, task_id: str) -> ProductionTask:
        """用户执行动作登记持续推进意图；只更新执行元数据，不替换冻结输入。"""
        with self.unit() as work:
            task = work.tasks.get(task_id)
            if task.status not in {"queued", "failed"} or task.auto_run:
                return task
            updated = task.model_copy(update={"auto_run": True, "updated_at": utcnow()})
            work.tasks.save(updated)
            return updated

    def fail_interrupted(self, task_id: str, reason: str) -> ProductionTask:
        """调度/准备异常结束准确任务；撤销租约，保留已提交产物和历史尝试。"""
        with self.unit() as work:
            task = work.tasks.get(task_id)
            if task.status not in {"queued", "running", "failed"}:
                return task
            updated = task.model_copy(
                update={
                    "status": "failed",
                    "error": reason,
                    "lease_token": "",
                    "lease_until": "",
                    "updated_at": utcnow(),
                }
            )
            work.tasks.save(updated)
            return updated

    def control(self, request: ControlProductionTask) -> ProductionTask:
        """撤销租约或从冻结阶段创建后继；历史产物和尝试不改写，不执行模型。"""
        with self.unit() as work:
            task = work.tasks.get(request.task_id)
            children = [t for t in work.tasks.list() if t.parent_task_id == task.id]
            retry_id = "production-" + hashlib.sha256((task.id + ":restart").encode()).hexdigest()[:32]
            if request.action in {"resume", "retry"} and children:
                if len(children) == 1 and children[0].id == retry_id:
                    return children[0]
                raise ValueError("任务已有后继，请操作后继任务")
            if task.status != request.expected_status or task.attempt != request.expected_attempt:
                raise ValueError("任务状态已变化，请刷新后操作")
            if children:
                raise ValueError("任务已有后继，请操作当前阶段")
            now = utcnow()
            if request.action in {"pause", "stop"}:
                allowed = (
                    {"queued", "running"}
                    if request.action == "pause"
                    else {"queued", "running", "paused", "failed", "blocked"}
                )
                if task.status not in allowed:
                    raise ValueError("当前任务不可暂停或停止")
                updated = task.model_copy(
                    update={
                        "status": "paused" if request.action == "pause" else "cancelled",
                        "lease_token": "",
                        "lease_until": "",
                        "updated_at": now,
                    }
                )
            else:
                allowed = {"paused"} if request.action == "resume" else {"failed", "blocked", "cancelled"}
                if task.status not in allowed:
                    raise ValueError("当前任务不可继续或重试")
                if task.story_id:
                    story = work.stories.get(task.story_id)
                    if task.phase == "article-revision" and task.web_search_enabled:
                        from agent4pharma.workflows.article_product_versions import bound_product

                        edit_context = ProductionContext.model_validate_json(task.input_json)
                        bound_product(work, edit_context.article_edit.package_id)
                        if story.revision != task.expected_revision:
                            raise ValueError("图文上下文已变化，请重新提交修改意见")
                    elif story.status != "active" or story.revision != task.expected_revision:
                        raise ValueError("任务书已变化或选题已暂停、取消，请查看当前选题与任务书")
                    if any(
                        t.story_id == story.id
                        and t.status in {"queued", "running", "paused"}
                        and t.id != task.id
                        for t in work.tasks.list()
                    ):
                        raise ValueError("选题已有其他待执行任务")
                context = json.loads(task.input_json)
                # 已保存底稿和显式候选属于保存点；会话中的未保存内容不属于。
                # model_copy保留revision_workspace_json及原origin_task_id；候选绑定原稿和问题hash不变。
                context["parent_task_id"] = task.id
                recovered = {}
                if task.context_contract:
                    writing = json.loads(task.writing_research_json or "[]")
                    background = json.loads(task.background_research_json or "[]")
                    if writing:
                        context["writing_research"] = writing[-1]
                    if background:
                        context["background_research"] = [
                            *context.get("background_research", []),
                            background[-1],
                        ]
                    recovered = {"writing_research_json": "", "background_research_json": ""}
                updated = task.model_copy(
                    update={
                        **recovered,
                        "id": retry_id,
                        "parent_task_id": task.id,
                        "actor_id": f"{task.role}:{retry_id}",
                        "input_json": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
                        "status": "queued",
                        "attempt": 0,
                        "lease_token": "",
                        "lease_until": "",
                        "reads": 0,
                        "figure_queries": (),
                        "figure_pages": (),
                        "figure_inspections": (),
                        "citation_lookups": (),
                        "skill_reads": (),
                        "document_reads": {},
                        "result_ref": "",
                        "error": "",
                        "editing_result_json": "",
                        "reader_observation_json": "",
                        "reader_observation_created_at": "",
                        "reader_brief_read": False,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            work.tasks.save(updated)
            work.tasks.event(
                uuid.uuid4().hex, task.story_id, "operator", request.action, f"{task.id} -> {updated.id}", now
            )
            return updated

    def claim(self, task_id: str) -> ProductionTask:
        """原子取得有限执行权；旧租约过期可恢复，但次数不得超过授权。"""
        with self.unit() as work:
            task = work.tasks.get(task_id)
            now = datetime.now(timezone.utc)
            if task.status == "running" and datetime.fromisoformat(task.lease_until) > now:
                raise ValueError("制作任务正在执行")
            if task.status not in {"queued", "failed", "running"}:
                raise ValueError("制作任务不可领取")
            if any(t.parent_task_id == task.id for t in work.tasks.list()):
                raise ValueError("任务已有后继或恢复分支，请执行后继任务")
            if task.attempt >= task.limits.max_attempts:
                raise ValueError("制作任务尝试额度耗尽")
            task = task.model_copy(
                update={
                    "status": "running",
                    "attempt": task.attempt + 1,
                    "lease_token": uuid.uuid4().hex,
                    "lease_until": (now + timedelta(seconds=task.limits.timeout_seconds)).isoformat(),
                    "reads": 0,
                    "citation_lookups": (),
                    "skill_reads": (),
                    "document_reads": {},
                    "error": "",
                    "updated_at": utcnow(),
                }
            )
            work.tasks.save(task)
            return task

    def execute(self, task_id: str, runner: ProductionRunner) -> ProductionTask:
        """事务外执行绑定角色；异常脱敏，合法提交后的晚到错误不覆盖成功。"""
        try:
            task = self.claim(task_id)
        except ValueError:
            current = self.get(task_id)
            if current.status in {"paused", "cancelled"}:
                return current
            raise
        try:
            runner.run(task)
            if self.get(task.id).status == "running":
                self.fail(task, "模型未提交合法结构化产物")
        except Exception as exc:
            self.fail(
                task,
                exc.public_message
                if isinstance(exc, ProductionModelFailure)
                else "角色执行失败：" + type(exc).__name__,
            )
        return self.get(task.id)

    def fail(self, original: ProductionTask, reason: str) -> None:
        """只有原租约可以将正在运行的任务标失败。"""
        with self.unit() as work:
            task = work.tasks.get(original.id)
            if task.status == "running" and task.lease_token == original.lease_token:
                work.tasks.save(
                    task.model_copy(update={"status": "failed", "error": reason, "updated_at": utcnow()})
                )


def require_lease(task: ProductionTask, token: str, role: str) -> None:
    """工具每次调用校验绑定角色、租约和时限，最终聊天文本不能绕过。"""
    if task.role != role or task.status != "running" or task.lease_token != token:
        raise ValueError("角色或制作任务租约无效")
    if datetime.fromisoformat(task.lease_until) <= datetime.now(timezone.utc):
        raise ValueError("制作任务租约已过期")
