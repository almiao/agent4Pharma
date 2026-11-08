"""制作产物的轻量只读投影；中控不批量读取正文或将历史报告当当前审核结论。"""

from typing import Literal

from pydantic import BaseModel, Field

from agent4pharma.modules.editorial.commission_contracts import EditorialDecisionRecord
from agent4pharma.modules.publishing.release_contracts import ReleaseReceipt
from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.runtime.workflow_observation import ObservationSink
from agent4pharma.workflows.production_dependencies import ProductionContext, ProductionUnit
from agent4pharma.workflows.production_intermediates import (
    ProductionIntermediateSummary,
    summarize_intermediates,
)
from agent4pharma.workflows.production_release_validation import require_current_package


def production_task_label(task: ProductionTask) -> str:
    """显示初稿/修订的真实工作目的，不改变冻结阶段、任务身份或执行行为。"""
    if task.phase == "draft-writing":
        context = ProductionContext.model_validate_json(task.input_json)
        return "稿件返修" if context.draft is not None else "初稿撰写"
    return {
        "topic-selection": "主编选题",
        "reading-review": "阅读评审",
        "fact-check": "事实核验",
        "layout-production": "图文编排",
        "article-revision": "协作改稿",
    }.get(
        task.phase,
        {"editor": "主编选题", "writer": "作者写作", "reviewer": "事实核验", "publisher": "图文编排"}.get(
            task.role, task.phase or task.role
        ),
    )


class ProductionArtifactSummary(BaseModel):
    """一个持久化产物的身份与版本摘要；本地包是否可导出仍由导出用例检查。"""

    id: str
    kind: Literal["draft", "review", "reader_review", "package"]
    story_id: str
    title: str
    version: str
    status: str
    reference_id: str
    created_at: str
    current: bool = False
    reason: str = ""
    validity: str = ""


class ProductionTaskLink(BaseModel):
    """任务的只读关联与当前性，不将历史失败当作当前选题阻断。"""

    task_id: str
    candidate_ids: list[str] = Field(default_factory=list)
    assessment_ids: list[str] = Field(default_factory=list)
    draft_id: str = ""
    review_ids: list[str] = Field(default_factory=list)
    current: bool
    rework: bool = False


class ProductionArtifactIndex(BaseModel):
    """同一工作单元读取的产物摘要；不包含稿件正文、证据或渠道包内容。"""

    items: list[ProductionArtifactSummary]
    task_links: list[ProductionTaskLink] = Field(default_factory=list)
    decisions: list[EditorialDecisionRecord] = Field(default_factory=list)
    receipts: list[ReleaseReceipt] = Field(default_factory=list)
    intermediates: list[ProductionIntermediateSummary] = Field(default_factory=list)


def summarize_production_artifacts(
    work: ProductionUnit, observations: ObservationSink | None = None
) -> ProductionArtifactIndex:
    """通过业务仓储公开契约投影已有产物，不修改状态或执行发布有效性校验。"""
    drafts = work.drafts.list()
    stories = {s.id: s for s in work.stories.list()}
    latest = {}
    for draft in drafts:
        if draft.story_id not in latest or draft.number > latest[draft.story_id].number:
            latest[draft.story_id] = draft
    current_drafts = {
        d.id
        for d in latest.values()
        if d.brief_version == stories[d.story_id].brief_version and stories[d.story_id].status != "cancelled"
    }
    reviews = work.reviews.list()
    latest_reviews = {}
    for report in reviews:
        latest_reviews[report.draft_id] = report.id
    titles = {draft.id: draft.submission.title for draft in drafts}
    items = [
        ProductionArtifactSummary(
            id=draft.id,
            kind="draft",
            story_id=draft.story_id,
            title=draft.submission.title,
            version=f"稿件 v{draft.number} · 任务书 v{draft.brief_version}",
            status="saved",
            reference_id="",
            created_at=draft.created_at,
            current=draft.id in current_drafts,
        )
        for draft in drafts
    ]
    items.extend(
        ProductionArtifactSummary(
            id=report.id,
            kind="review",
            story_id=report.story_id,
            title=titles.get(report.draft_id, report.draft_id),
            version=f"{report.draft_id} · 任务书 v{report.brief_version}",
            status=report.submission.decision,
            reference_id=report.draft_id,
            created_at=report.created_at,
            current=report.draft_id in current_drafts
            and latest_reviews[report.draft_id] == report.id
            and report.draft_hash == latest[report.story_id].content_hash
            and report.brief_version == latest[report.story_id].brief_version,
        )
        for report in reviews
    )
    items.extend(
        ProductionArtifactSummary(
            id=report.id,
            kind="reader_review",
            story_id=report.story_id,
            title=titles.get(report.draft_id, report.draft_id),
            version=f"{report.draft_id} · 阅读品质",
            status=report.submission.decision,
            reference_id=report.draft_id,
            created_at=report.created_at,
            current=report.draft_id in current_drafts
            and report.draft_hash == latest[report.story_id].content_hash,
        )
        for report in work.reviews.reader_list()
    )
    for package in work.releases.packages():
        reason = ""
        try:
            require_current_package(work, package)
        except ValueError as exc:
            reason = str(exc)
        items.append(
            ProductionArtifactSummary(
                id=package.id,
                kind="package",
                story_id=package.story_id,
                title=titles.get(package.draft_id, package.draft_id),
                version=f"{package.draft_id} · 选题修订 {package.story_revision}",
                status="local_preview",
                validity="invalid" if reason else "valid",
                reference_id=package.review_id,
                created_at=package.created_at,
                current=not reason,
                reason=reason,
            )
        )
    tasks = work.tasks.list()
    completed_editorials = {
        task.id: task.updated_at for task in tasks if task.role == "editor" and task.status == "succeeded"
    }
    links = []
    for task in tasks:
        context = ProductionContext.model_validate_json(task.input_json)
        story = stories.get(task.story_id)
        links.append(
            ProductionTaskLink(
                task_id=task.id,
                candidate_ids=[c.id for c in context.candidates],
                assessment_ids=[c.assessment_id for c in context.candidates if c.assessment_id],
                draft_id=context.draft.id if context.draft else "",
                review_ids=[r.id for r in context.reviews] + [r.id for r in context.reader_reviews],
                current=not task.story_id
                or bool(story and story.status == "active" and task.expected_revision == story.revision),
                rework=task.role == "writer" and bool(context.reviews or context.reader_reviews),
            )
        )
    return ProductionArtifactIndex(
        items=items,
        task_links=links,
        decisions=[
            record.model_copy(update={"created_at": completed_editorials.get(record.task_id, "")})
            for record in work.stories.decisions()
        ],
        receipts=work.releases.receipts(),
        intermediates=summarize_intermediates(tasks, observations),
    )
