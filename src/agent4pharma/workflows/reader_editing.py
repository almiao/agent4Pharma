"""阅读评审的分步读取、准确版本报告与有限返修交接。"""

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, Field

from agent4pharma.modules.content.draft_contracts import ReaderRevisionResponse
from agent4pharma.modules.editorial.commission_contracts import CommissionBrief
from agent4pharma.modules.review.reader_review_contracts import (
    ReaderObservation,
    ReaderReviewReport,
    ReaderReviewSubmission,
    validate_reader_review,
)
from agent4pharma.runtime.context_records import ContextPayload
from agent4pharma.runtime.skill_resources import SkillResource
from agent4pharma.shared.clock import utcnow
from agent4pharma.workflows.production_dependencies import ProductionContext, ProductionUnit
from agent4pharma.workflows.production_evidence import require_skill_reads
from agent4pharma.workflows.production_handoff import advance_story
from agent4pharma.workflows.production_queue import ProductionRoleDefinition, finish_task, queue_role
from agent4pharma.workflows.production_revision_budget import can_queue_revision
from agent4pharma.workflows.production_story_state import active_story
from agent4pharma.workflows.production_tasks import require_lease


class ReaderDraftView(BaseModel):
    """初次只给准确成稿，不给作者底稿、自审结论或任务书答案。"""

    draft_id: str
    draft_hash: str
    title: str
    body: str


class ReaderReviewContext(ReaderDraftView, ContextPayload):
    """盲读工作集，包含全文、冻结规范和本任务已保存的首次观察。"""

    resources: list[SkillResource]
    saved_observation: ReaderObservation | None = None


def reader_draft(work: ProductionUnit, task_id: str, token: str) -> ReaderDraftView:
    """验证租约及阶段后返回成稿；规范通过冻结资源工具读取。"""
    task = work.tasks.get(task_id)
    require_lease(task, token, "reviewer")
    if task.phase != "reading-review":
        raise ValueError("仅阅读评审可读取盲读成稿")
    active_story(work, task)
    context = ProductionContext.model_validate_json(task.input_json)
    if context.draft is None:
        raise ValueError("缺少准确待审稿件")
    return ReaderDraftView(
        draft_id=context.draft.id,
        draft_hash=context.draft.content_hash,
        title=context.draft.submission.title,
        body=context.draft.submission.body,
    )


def reader_review_context(work: ProductionUnit, task_id: str, token: str) -> ReaderReviewContext:
    """交付完整盲读稿与全部必读资源，记录宿主已处理的冻结资源。"""
    draft = reader_draft(work, task_id, token)
    task = work.tasks.get(task_id)
    if "get_reader_review_context" not in task.context_tools or task.newsroom_skills is None:
        raise ValueError("当前任务未冻结合并阅读评审入口")
    resources = [task.newsroom_skills.read(name) for name in task.required_resources]
    observation = (
        ReaderObservation.model_validate_json(task.reader_observation_json)
        if task.reader_observation_json
        else None
    )
    skill_reads = tuple(sorted(set(task.skill_reads).union(task.required_resources)))
    if skill_reads != task.skill_reads:
        work.tasks.save(task.model_copy(update={"skill_reads": skill_reads}))
    return ReaderReviewContext(**draft.model_dump(), resources=resources, saved_observation=observation)


class ReaderReportIndex(BaseModel):
    """已结束阅读轮次的身份和开闭状态，报告原文仅按需展开。"""

    report_id: str
    draft_id: str
    draft_hash: str
    decision: str
    issue_ids: list[str]
    closed_issue_ids: list[str]


class ReaderBriefView(BaseModel):
    """阅读观察保存后才提供任务书和前轮阅读意见，仍不提供作者自审与底稿。"""

    review_mode: Literal["initial", "reconsideration"]
    brief: CommissionBrief
    editorial_reason: str
    previous_reviews: list[ReaderReviewReport]
    author_responses: list[ReaderRevisionResponse]
    review_history: list[ReaderReportIndex] = Field(default_factory=list)


class ReaderBriefContext(ReaderBriefView, ContextPayload):
    """首次观察后的工作集，使用通用上下文回执追踪交付。"""


def reader_brief(work: ProductionUnit, task_id: str, token: str) -> ReaderBriefView:
    """分步开放任务要求，禁止用作者底稿替正文补义。"""
    reader_draft(work, task_id, token)
    task = work.tasks.get(task_id)
    if not task.reader_observation_json:
        raise ValueError("先保存首次阅读观察，再核对任务书")
    context = ProductionContext.model_validate_json(task.input_json)
    if not task.reader_brief_read:
        work.tasks.save(task.model_copy(update={"reader_brief_read": True}))
    compact = task.revision_window_policy is not None and "read_reader_review" in task.context_tools
    return ReaderBriefView(
        review_mode=context.review_mode,
        brief=context.story.brief,
        editorial_reason=context.story.editorial_reason,
        previous_reviews=context.reader_reviews[-1:] if compact else context.reader_reviews,
        review_history=[
            ReaderReportIndex(
                report_id=r.id,
                draft_id=r.draft_id,
                draft_hash=r.draft_hash,
                decision=r.submission.decision,
                issue_ids=[i.id for i in r.submission.issues],
                closed_issue_ids=r.submission.closed_issue_ids,
            )
            for r in context.reader_reviews[:-1]
        ]
        if compact
        else [],
        author_responses=context.draft.submission.reader_responses,
    )


def read_reader_review(work: ProductionUnit, task_id: str, token: str, report_id: str) -> ReaderReviewReport:
    """观察和任务书读取后才允许展开一个准确历史阅读报告；不访问作者研究或事实结论。"""
    reader_draft(work, task_id, token)
    task = work.tasks.get(task_id)
    if not task.reader_brief_read or "read_reader_review" not in task.context_tools:
        raise ValueError("先完成盲读并读取本轮问题目录")
    context = ProductionContext.model_validate_json(task.input_json)
    report = next((r for r in context.reader_reviews if r.id == report_id), None)
    if report is None:
        raise ValueError("报告不属于当前冻结任务")
    return report


def save_reader_observation(work: ProductionUnit, task_id: str, token: str, result: ReaderObservation) -> str:
    """仅保存第一次阅读结果；重复同内容幂等，不允许读任务书后改写观察。"""
    reader_draft(work, task_id, token)
    task = work.tasks.get(task_id)
    payload = result.model_dump_json()
    if task.reader_observation_json and task.reader_observation_json != payload:
        raise ValueError("首次阅读观察已经保存，不能覆盖")
    if not task.reader_observation_json:
        work.tasks.save(
            task.model_copy(
                update={"reader_observation_json": payload, "reader_observation_created_at": utcnow()}
            )
        )
    return "首次阅读观察已保存；继续核对任务书并提交阅读报告"


def record_reader_observation(
    work: ProductionUnit, task_id: str, token: str, result: ReaderObservation | None
) -> ReaderBriefContext:
    """在同一工作单元先持久化观察，再开放任务书；省略观察只用于恢复。"""
    task = work.tasks.get(task_id)
    require_lease(task, token, "reviewer")
    if "record_reader_observation" not in task.context_tools:
        raise ValueError("当前任务未冻结合并阅读评审入口")
    require_skill_reads(task)
    if result is not None:
        save_reader_observation(work, task_id, token, result)
    brief = reader_brief(work, task_id, token)
    return ReaderBriefContext(**brief.model_dump())


def submit_reader_review(
    work: ProductionUnit,
    definitions: Mapping[str, ProductionRoleDefinition],
    task_id: str,
    token: str,
    result: ReaderReviewSubmission,
) -> str:
    """原子保存通过/返修结论并排队后继；超限仍保留返修要求，不放行。"""
    view = reader_draft(work, task_id, token)
    task = work.tasks.get(task_id)
    require_skill_reads(task)
    if not task.reader_observation_json or task.newsroom_skills is None:
        raise ValueError("须先保存首次阅读观察并读取冻结评审资源")
    if not task.reader_brief_read:
        raise ValueError("须在首次观察后读取任务书，再提交阅读结论")
    context = ProductionContext.model_validate_json(task.input_json)
    if context.reader_policy != "reader-quality-v1":
        raise ValueError("当前任务未绑定阅读品质策略")
    previous = context.reader_reviews[-1:] if context.reader_reviews else []
    previous_required = {i.id for r in previous for i in r.submission.issues if i.severity == "required"}
    validate_reader_review(result, view.title + "\n" + view.body, previous_required)
    story = active_story(work, task)
    if context.draft.author_id == task.actor_id:
        raise ValueError("作者不能审核自己的阅读品质")
    report = ReaderReviewReport(
        id="reader-review-" + task.id,
        story_id=story.id,
        draft_id=view.draft_id,
        draft_hash=view.draft_hash,
        brief_version=story.brief_version,
        reviewer_id=task.actor_id,
        audience_hash=task.newsroom_skills.read("standards/audience.md").sha256,
        rubric_hash=task.newsroom_skills.content_hash,
        observation=ReaderObservation.model_validate_json(task.reader_observation_json),
        submission=result,
        created_at=utcnow(),
    )
    revision_allowed = result.decision == "changes_requested" and can_queue_revision(
        work, task, story, "reader"
    )
    work.reviews.save_reader(report)
    story = advance_story(work, story)
    if result.decision == "ready" or (result.decision == "changes_requested" and revision_allowed):
        phase = "fact-check" if result.decision == "ready" else "draft-writing"
        queue_role(
            work,
            definitions,
            "reviewer" if result.decision == "ready" else "writer",
            context.model_copy(
                update={
                    "story": story,
                    "phase": phase,
                    "parent_task_id": task.id,
                    "reader_reviews": [*context.reader_reviews, report],
                }
            ),
            task.limits,
        )
    else:
        work.stories.save(
            story.model_copy(update={"revision": story.revision + 1, "status": "deferred"}),
            story.revision,
        )
    finish_task(work, task, report.id)
    return report.id
