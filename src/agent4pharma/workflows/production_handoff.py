"""四角色的原子业务交接；模型只能提交本任务结构化产物，不能改流程状态。"""

import hashlib
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager

from agent4pharma.modules.content.draft_contracts import (
    DraftRevision,
    DraftSubmission,
)
from agent4pharma.modules.content.draft_revision import DraftRevisionSubmission, apply_draft_revision
from agent4pharma.modules.content.revision_workspace import CandidateCommit
from agent4pharma.modules.editorial.commission_contracts import (
    EditorialDecision,
    Story,
    validate_decision,
)
from agent4pharma.modules.publishing.article_layout import ArticlePresentation
from agent4pharma.modules.publishing.release_contracts import (
    ChannelPackage,
    ChannelSubmission,
    render_package,
)
from agent4pharma.modules.review.news_acceptance import validate_news_acceptance
from agent4pharma.modules.review.review_contracts import ReviewReport, ReviewSubmission, validate_review
from agent4pharma.shared.clock import utcnow
from agent4pharma.workflows.draft_submission_validation import validate_submission
from agent4pharma.workflows.production_dependencies import ProductionContext, ProductionUnit
from agent4pharma.workflows.production_evidence import bound_task_evidence, require_skill_reads, task_evidence
from agent4pharma.workflows.production_queue import (
    PRODUCTION_TRANSITIONS,
    ProductionRoleDefinition,
    finish_task,
    queue_role,
)
from agent4pharma.workflows.production_revision_budget import can_queue_revision
from agent4pharma.workflows.production_story_state import active_story
from agent4pharma.workflows.production_tasks import require_lease
from agent4pharma.workflows.revision_context import is_patch_revision
from agent4pharma.workflows.revision_workspace_state import committed_candidate


def advance_story(work: ProductionUnit, story: Story) -> Story:
    """每次交接提升聚合版本，使其他过期尝试不能提交。"""
    updated = story.model_copy(update={"revision": story.revision + 1, "updated_at": utcnow()})
    work.stories.save(updated, story.revision)
    return updated


class ProductionHandoff:
    """立项、稿件、审核和本地渠道包的交接用例。"""

    def __init__(
        self,
        unit: Callable[[], AbstractContextManager[ProductionUnit]],
        definitions: Mapping[str, ProductionRoleDefinition],
    ) -> None:
        self.unit, self.definitions = unit, definitions

    def editorial(self, task_id: str, token: str, result: EditorialDecision) -> str:
        """校验候选，原子保存任务书并排队内容角色；其他选题不阻止立项。"""
        with self.unit() as work:
            task = work.tasks.get(task_id)
            require_lease(task, token, "editor")
            require_skill_reads(task)
            context = ProductionContext.model_validate_json(task.input_json)
            context.evidence = task_evidence(task)
            validate_decision(
                result,
                {candidate.id for candidate in context.candidates},
                binary=task.binary_editorial_decision,
            )
            work.stories.decision(task.id, result)
            if result.decision != "commission":
                finish_task(work, task, result.decision)
                return result.decision
            selected = [c for c in context.candidates if c.id in result.candidate_ids]
            story = Story(
                id="story-" + task.id,
                brief=result.brief,
                editorial_reason=result.reason,
                candidate_ids=result.candidate_ids,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            work.stories.save(story, 0)
            for candidate in selected:
                for evidence in candidate.evidence:
                    work.drafts.add_evidence(story.id, evidence)
            for evidence in context.evidence:
                work.drafts.add_evidence(story.id, evidence)
            queue_role(
                work,
                self.definitions,
                PRODUCTION_TRANSITIONS["commission"].target,
                ProductionContext(
                    candidates=selected,
                    phase="draft-writing" if task.phase else "",
                    parent_task_id=task.id if task.phase else "",
                    story=story,
                    evidence=bound_task_evidence(work, task),
                ),
                task.limits,
            )
            finish_task(work, task, story.id)
            return story.id

    def content(
        self, task_id: str, token: str, result: DraftSubmission | DraftRevisionSubmission | CandidateCommit
    ) -> str:
        """验证真实引用、必改项回应和全文要求，再追加稿件并排队独立核验。"""
        with self.unit() as work:
            task = work.tasks.get(task_id)
            require_lease(task, token, "writer")
            require_skill_reads(task)
            if task.phase and task.phase != "draft-writing":
                raise ValueError("研究阶段不能提交稿件")
            if task.phase == "article-revision":
                raise ValueError("协作编辑只能提交候选，不能直接覆盖稿件")
            story = active_story(work, task)
            context = ProductionContext.model_validate_json(task.input_json)
            if isinstance(result, DraftRevisionSubmission):
                if not is_patch_revision(task) or context.draft is None:
                    raise ValueError("当前任务不接受局部返修提交")
                result = apply_draft_revision(context.draft, result)
            if isinstance(result, CandidateCommit):
                result = committed_candidate(task, context, result.expected_version)
            elif task.context_contract == "node-context-v2" and is_patch_revision(task):
                raise ValueError("新返修须保存候选并通过submit_revision_candidate提交")
            result = validate_submission(work, task, context, result)
            evidence = bound_task_evidence(work, task)
            previous = [draft for draft in work.drafts.list() if draft.story_id == story.id]
            draft = DraftRevision(
                id="draft-" + task.id,
                reader_policy=context.reader_policy,
                story_id=story.id,
                brief_version=story.brief_version,
                number=len(previous) + 1,
                author_id=task.actor_id,
                submission=result,
                evidence=evidence,
                content_hash=hashlib.sha256(result.model_dump_json().encode()).hexdigest(),
                created_at=utcnow(),
            )
            work.drafts.save(draft)
            story = advance_story(work, story)
            queue_role(
                work,
                self.definitions,
                PRODUCTION_TRANSITIONS["draft"].target,
                ProductionContext(
                    story=story,
                    phase=("reading-review" if context.reader_policy else "fact-check") if task.phase else "",
                    parent_task_id=task.id if task.phase else "",
                    writing_research=context.writing_research,
                    draft=draft,
                    evidence=evidence,
                    reviews=context.reviews,
                    reader_reviews=context.reader_reviews,
                    reader_policy=context.reader_policy,
                ),
                task.limits,
            )
            finish_task(work, task, draft.id)
            return draft.id

    def review(self, task_id: str, token: str, result: ReviewSubmission) -> str:
        """准确稿件有必改即返修，无必改才通过；额度耗尽保留未通过结论。"""
        with self.unit() as work:
            task = work.tasks.get(task_id)
            require_lease(task, token, "reviewer")
            if task.phase == "reading-review":
                raise ValueError("阅读评审必须提交独立阅读品质报告")
            require_skill_reads(task)
            story = active_story(work, task)
            context = ProductionContext.model_validate_json(task.input_json)
            draft = context.draft
            if draft is None or draft.brief_version != story.brief_version:
                raise ValueError("审核稿件或任务书版本无效")
            if context.reader_policy or draft.reader_policy:
                from agent4pharma.workflows.reader_readiness import require_reader_ready

                require_reader_ready(context)
            validate_review(result, draft.author_id, task.actor_id)
            edited = next(
                (p for p in reversed(work.releases.packages()) if p.draft_id == draft.id and p.edit_pending),
                None,
            )
            review_text = draft.submission.title + "\n" + draft.submission.body
            if edited and edited.presentation:
                review_text += "\n" + "\n".join(f.selection.caption for f in edited.presentation.figures)
            if task.newsroom_policy == "news-focus-v2":
                validate_news_acceptance(
                    result,
                    review_text,
                    {e.id: e.text for e in bound_task_evidence(work, task)},
                    [requirement for requirement in (story.brief.angle, story.brief.boundary) if requirement],
                )
            report = ReviewReport(
                id="review-" + task.id,
                story_id=story.id,
                draft_id=draft.id,
                draft_hash=draft.content_hash,
                brief_version=story.brief_version,
                reviewer_id=task.actor_id,
                submission=result,
                created_at=utcnow(),
            )
            revision_allowed = result.decision == "changes_requested" and can_queue_revision(
                work, task, story, "fact"
            )
            work.reviews.save(report)
            story = advance_story(work, story)

            if result.decision == "pass" and edited:
                work.releases.save(
                    edited.model_copy(
                        update={
                            "id": "verified-" + task.id,
                            "edit_parent_id": edited.id,
                            "edit_pending": False,
                            "review_id": report.id,
                            "story_revision": story.revision + 1,
                            "created_at": utcnow(),
                        }
                    )
                )
                work.stories.save(
                    story.model_copy(update={"status": "completed", "revision": story.revision + 1}),
                    story.revision,
                )
            elif result.decision == "pass":
                queue_role(
                    work,
                    self.definitions,
                    PRODUCTION_TRANSITIONS["pass"].target,
                    ProductionContext(
                        story=story,
                        draft=draft,
                        evidence=draft.evidence,
                        phase="layout-production" if task.phase else "",
                        parent_task_id=task.id if task.phase else "",
                        writing_research=context.writing_research,
                        reviews=[report],
                        reader_reviews=context.reader_reviews,
                        reader_policy=context.reader_policy,
                    ),
                    task.limits,
                )
            elif result.decision == "changes_requested" and revision_allowed:
                queue_role(
                    work,
                    self.definitions,
                    PRODUCTION_TRANSITIONS["changes_requested"].target,
                    ProductionContext(
                        story=story,
                        draft=draft,
                        evidence=bound_task_evidence(work, task),
                        phase="draft-writing" if task.phase else "",
                        parent_task_id=task.id if task.phase else "",
                        writing_research=context.writing_research,
                        reviews=[report],
                        reader_reviews=context.reader_reviews,
                        reader_policy=context.reader_policy,
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

    def publishing(
        self,
        task_id: str,
        token: str,
        result: ChannelSubmission,
        prepared: tuple[str, ArticlePresentation] | None = None,
    ) -> str:
        """只从当前审核合格稿生成本地包，不允许模型直接发送或改写原文。"""
        with self.unit() as work:
            task = work.tasks.get(task_id)
            require_lease(task, token, "publisher")
            require_skill_reads(task)
            story = active_story(work, task)
            context = ProductionContext.model_validate_json(task.input_json)
            draft = context.draft
            if draft is None or not context.reviews:
                raise ValueError("缺少稿件或审核")
            if context.reader_policy or draft.reader_policy:
                from agent4pharma.workflows.reader_readiness import require_reader_ready

                require_reader_ready(context)
            report = context.reviews[-1]
            if (
                report.submission.decision != "pass"
                or report.draft_hash != draft.content_hash
                or report.brief_version != story.brief_version
            ):
                raise ValueError("审核已经失效")
            body, content_hash = render_package(draft.submission.title, draft.submission.body, result.channel)
            if (result.figures or result.emphasis) and prepared is None:
                raise ValueError("图文包须先通过绑定原图制作")
            if prepared:
                if prepared[1].source_body_hash != hashlib.sha256(draft.submission.body.encode()).hexdigest():
                    raise ValueError("准备的图文不属于当前稿件")
                body = prepared[0]
                content_hash = hashlib.sha256(body.encode()).hexdigest()
            package = ChannelPackage(
                id="package-" + task.id,
                presentation=prepared[1].model_copy(
                    update={"selection_task_id": task.id, "model_calls": None, "total_tokens": None}
                )
                if prepared
                else None,
                story_id=story.id,
                story_revision=story.revision + 1,
                draft_id=draft.id,
                review_id=report.id,
                channel=result.channel,
                body=body,
                content_hash=content_hash,
                release_note=result.release_note,
                created_at=utcnow(),
            )
            work.releases.save(package)
            work.stories.save(
                story.model_copy(
                    update={"revision": story.revision + 1, "status": "completed", "updated_at": utcnow()}
                ),
                story.revision,
            )
            finish_task(work, task, package.id)
            return package.id
