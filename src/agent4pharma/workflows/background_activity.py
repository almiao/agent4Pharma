"""首页后台活动的只读投影；合并父子任务，过期运行记录不冒充存活进程。"""

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlencode

from pydantic import BaseModel

from agent4pharma.modules.discovery.daily_batch_runs import DailyBatchRun
from agent4pharma.modules.discovery.intelligence_contracts import IntelligenceTask, IntelligenceTaskSummary
from agent4pharma.modules.discovery.paper_selection import PaperSelectionRun
from agent4pharma.modules.discovery.paper_selection_view import selection_view
from agent4pharma.modules.maintenance.models import InvestigationJob
from agent4pharma.modules.materials.paper_download_jobs import PaperDownloadJob
from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.workflows.production_views import production_task_label


class BackgroundActivity(BaseModel):
    """一个业务级任务的可展示状态；不包含提示词、口令、租约或连接地址。"""

    id: str
    kind: str
    title: str
    status: Literal["running", "queued", "waiting", "attention", "unknown", "complete", "paused"]
    current_step: str
    updated_at: str = ""
    href: str
    completed: int | None = None
    total: int | None = None
    expires_at: str = ""


class BackgroundActivitySnapshot(BaseModel):
    """本机应用全局任务范围；计数按业务任务而非论文数或操作系统进程。"""

    as_of: str
    items: list[BackgroundActivity]
    counts: dict[str, int]
    scope: str = "全局应用任务；摘要批次合并计数。运行状态来自任务记录，非系统进程清单。"


def _href(stage: str, work: str, **values: str) -> str:
    return "/console?" + urlencode(
        {
            "view": "workspace",
            "stage": "selection",
            "work": work,
            **({"section": "records", "role": "intel"} if work != "selection" else {}),
            **values,
        }
    )


def selection_activity(
    run: PaperSelectionRun,
    tasks: Sequence[IntelligenceTask],
    summaries: Sequence[IntelligenceTaskSummary],
) -> BackgroundActivity:
    """整批摘要显示一项；当前论文仅作为步骤，不重复计为后台任务。"""
    # 首页只需进度，避免为每篇历史论文匹配机构并构造完整清单。
    view = selection_view(run, tasks, visible_record_ids=frozenset())
    progress = view["progress"]
    related = [s for s in summaries if s.selection_ref == run.id and s.stage == "abstract"]
    running = next((s for s in related if s.status == "running"), None)
    latest = max((s.updated_at for s in related), default=run.created_at)
    complete = run.phase == "fulltexts"
    local_running = run.processing_stage != "idle"
    if run.activity_updated_at:
        latest = max(latest, run.activity_updated_at)
    steps = {
        "local_abstracts": "本地 Decider 批量筛摘要（每批最多16篇）",
        "local_ranking": "本地 Decider 原生 logit 排序",
        "commercial_review": "商业模型复核本地前10篇，收敛到最多5篇",
    }
    return BackgroundActivity(
        id=run.id,
        kind="摘要筛选",
        title="摘要筛选 → 精选名单",
        status="complete"
        if complete
        else "paused"
        if run.paused
        else "running"
        if running or local_running
        else "waiting",
        current_step=(
            f"已生成精选名单，最多 {run.shortlist_limit} 篇"
            if complete
            else "已按用户要求暂停，未继续调用模型"
            if run.paused
            else steps[run.processing_stage]
            if local_running
            else "正在判断：" + running.title
            if running
            else "等待继续摘要筛选"
        ),
        updated_at=latest,
        href=_href("intel", "selection" if complete else "abstracts", batch=run.batch_id),
        completed=progress["completed"],
        total=progress["total"],
    )


def paper_continuation_activity(
    run: PaperSelectionRun, active_task_ids: set[str] | None = None
) -> list[BackgroundActivity]:
    """尚未领取模型任务的论文续处理队列；实际执行由情报/制作任务自身计数，避免重复。"""
    states = {"queued": "queued", "failed": "attention", "waiting": "waiting"}
    return [
        BackgroundActivity(
            id=run.id + ":" + item.request.evidence[0].id,
            kind="论文后续处理",
            title=item.request.evidence[0].title,
            status=states[item.continuation_status],
            current_step=item.continuation_error or "等待关联制作流程继续推进",
            updated_at=item.continuation_updated_at,
            href=_href("intel", "selection", batch=run.batch_id),
        )
        for item in run.items
        if item.continuation_status in states
        and not (
            item.continuation_status in {"queued", "waiting"}
            and item.continuation_task_id in (active_task_ids or set())
        )
    ]


def intelligence_activity(task: IntelligenceTaskSummary) -> BackgroundActivity:
    """独立评估保留准确任务链接，摘要子任务由调用方排除。"""
    status = {
        "running": "running",
        "queued": "queued",
        "pending": "waiting",
        "complete": "complete",
    }.get(task.status, "attention")
    return BackgroundActivity(
        id=task.id,
        kind="情报评估",
        title=task.title,
        status=status,
        current_step={
            "running": "模型评估中",
            "queued": "已排队",
            "waiting": "待执行",
            "complete": "评估完成",
            "attention": "评估未完成，请查看任务",
        }[status],
        updated_at=task.updated_at,
        href=_href("intel", "tasks", object=task.id, batch=task.parent_ref),
    )


def download_activity(job: PaperDownloadJob) -> BackgroundActivity:
    """从下载任务投影状态，认证和过期任务不被列成普通运行中。"""
    states = {
        "queued": "queued",
        "waiting_connection": "waiting",
        "downloading": "running",
        "validating": "running",
        "downloaded": "complete",
    }
    steps = {
        "queued": "等待获取 PDF",
        "waiting_connection": "等待浏览器确认",
        "downloading": "获取 PDF",
        "validating": "校验主文",
        "downloaded": "PDF 已校验",
        "manual_required": "需要人工处理",
        "failed": "下载未完成",
    }
    return BackgroundActivity(
        id=job.id,
        kind="PDF 下载",
        title=job.request.title,
        status=states.get(job.phase, "attention"),
        current_step=steps[job.phase],
        updated_at=job.updated_at,
        expires_at=job.expires_at,
        href=_href("intel", "selection", object=job.run_id + ":" + job.record_id, batch=job.request.batch_id),
    )


def production_activity(task: ProductionTask, *, has_successor: bool = False) -> BackgroundActivity:
    """制作任务仅暴露业务状态，排队不表示已授权执行模型。"""
    roles = {"editor": "主编选题", "writer": "作者写作", "reviewer": "稿件评审", "publisher": "图文编排"}
    states = {"running": "running", "queued": "queued", "succeeded": "complete", "paused": "paused"}
    return BackgroundActivity(
        id=task.id,
        kind=roles[task.role],
        title=production_task_label(task) + "任务",
        status="attention"
        if has_successor and task.status == "paused"
        else states.get(task.status, "attention"),
        current_step="已有后继任务，原尝试保留为历史"
        if has_successor
        else f"第 {task.attempt} 次执行"
        if task.status == "running"
        else ("已进入自动制作队列，等待前序完成" if task.auto_run else "旧排队记录，可开始制作")
        if task.status == "queued"
        else "请查看任务结果",
        updated_at=task.updated_at,
        expires_at=task.lease_until if task.status == "running" else "",
        href="/console?"
        + urlencode({"view": "workspace", "stage": "production", "story": task.story_id, "task": task.id})
        if task.story_id
        else "/console?" + urlencode({"view": "workspace", "stage": "production", "task": task.id}),
    )


def daily_batch_activity(run: DailyBatchRun) -> BackgroundActivity:
    """日常跑批的父任务投影；采集与筛选不重复计为两个运行中任务。"""
    steps = {
        "queued": "等待运行",
        "collecting": "来源采集与本地预选",
        "selecting": "本地排序与论文精选",
        "succeeded": "已生成下载清单",
        "partial": "部分完成，请查看来源覆盖与筛选状态",
        "failed": run.error,
    }
    return BackgroundActivity(
        id=run.id,
        kind="日常跑批",
        title=f"{run.day.isoformat()} 日常批次",
        status="queued"
        if run.status == "queued"
        else "running"
        if run.status in {"collecting", "selecting"}
        else "complete"
        if run.status == "succeeded"
        else "attention",
        current_step=steps[run.status],
        updated_at=run.updated_at,
        href="/console?"
        + urlencode(
            {
                "view": "workspace",
                "stage": "selection" if run.batch_id else "ingest",
                **({"batch": run.batch_id} if run.batch_id else {}),
            }
        ),
    )


def investigation_activity(job: InvestigationJob) -> BackgroundActivity:
    """已有调查任务显示实际状态；不重新开放来源维护执行入口。"""
    states = {"running": "running", "queued": "queued", "complete": "complete", "completed": "complete"}
    return BackgroundActivity(
        id=job.id,
        kind="来源调查",
        title=job.request.case.title or "来源调查",
        status=states.get(job.status, "attention"),
        current_step="调查任务：" + job.status,
        updated_at=job.updated_at,
        href="",
    )


def activity_snapshot(items: Sequence[BackgroundActivity], now: datetime) -> BackgroundActivitySnapshot:
    """最近一天结果与全部未完成任务；15 分钟无更新或租约过期转状态待确认，只读不修库。"""
    visible = []
    for source in items:
        item = source.model_copy()
        updated = datetime.fromisoformat(item.updated_at) if item.updated_at else None
        expired = bool(item.expires_at and datetime.fromisoformat(item.expires_at) <= now)
        if item.status == "running" and (expired or updated is None or now - updated > timedelta(minutes=15)):
            item.status = "unknown"
            item.current_step = "运行记录已过期，需确认任务是否仍在执行"
        if item.kind == "PDF 下载" and expired and item.status in {"queued", "waiting"}:
            item.status, item.current_step = "attention", "任务等待已超时，需人工重试"
        if item.status in {"complete", "attention"} and updated and now - updated > timedelta(days=1):
            continue
        visible.append(item)
    priority = {
        "running": 0,
        "queued": 1,
        "waiting": 2,
        "unknown": 3,
        "paused": 4,
        "attention": 5,
        "complete": 6,
    }
    visible.sort(key=lambda i: (priority[i.status], i.id))
    counts = {status: 0 for status in priority}
    counts.update(Counter(i.status for i in visible))
    return BackgroundActivitySnapshot(as_of=now.astimezone(UTC).isoformat(), items=visible, counts=counts)
