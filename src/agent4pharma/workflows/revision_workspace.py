"""返修候选工作区：小步保存、差异预检和准确恢复，正式交稿仍由 handoff 原子执行。"""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from agent4pharma.modules.content.draft_contracts import (
    ClaimEvidence,
    DraftSubmission,
    ReaderRevisionResponse,
)
from agent4pharma.modules.content.revision_workspace import (
    CandidateCommit,
    CandidateEdit,
    CandidateFeedback,
    CandidateReceipt,
    CandidateStep,
    CandidateVersion,
    RevisionWorkspace,
    candidate_hash,
    edit_candidate,
)
from agent4pharma.modules.editorial.commission_contracts import CommissionBrief
from agent4pharma.modules.review.review_issue_scope import ActiveIssueSet, resolve_active_issue_set
from agent4pharma.runtime.context_records import ContextPayload
from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.runtime.skill_resources import SkillResource
from agent4pharma.shared.clock import utcnow
from agent4pharma.workflows.draft_submission_validation import validate_submission
from agent4pharma.workflows.production_dependencies import ProductionContext, ProductionUnit
from agent4pharma.workflows.production_evidence import read_skill, require_skill_reads
from agent4pharma.workflows.production_story_state import active_story
from agent4pharma.workflows.production_tasks import require_lease
from agent4pharma.workflows.revision_workspace_state import committed_candidate, load_workspace

if TYPE_CHECKING:
    from agent4pharma.workflows.production_workbench import ProductionWorkbench


class IndexedClaim(BaseModel):
    """候选局部引用账本，索引仅对返回的候选版本有效。"""

    index: int
    value: ClaimEvidence


class RevisionEntry(ContextPayload):
    """首次通读当前候选与必改问题，引用账本和规范全文按需展开。"""

    draft_id: str
    base_content_hash: str
    version: int
    candidate_hash: str
    brief: CommissionBrief
    title: str
    body: str
    body_characters: int
    truncated: bool
    issues: ActiveIssueSet
    resources: list[SkillResource]
    optional_resources: list[str]
    next_action: str
    related_claims: list[IndexedClaim] = Field(default_factory=list)


class CandidateRead(ContextPayload):
    """按段落关键词或窗口读取候选；所有位置均相对准确候选版本。"""

    version: int
    section: str
    text: str = ""
    claims: list[IndexedClaim] = Field(default_factory=list)
    next_start: int | None = None
    total: int


class RevisionWorkspaceTools:
    """绑定准确任务租约的候选用例；每次保存原子更新，不排队或认证审核。"""

    def __init__(self, app: ProductionWorkbench, task_id: str, token: str) -> None:
        self.app, self.task_id, self.token = app, task_id, token

    def _load(self, work: ProductionUnit) -> tuple[ProductionTask, ProductionContext, RevisionWorkspace]:
        task = work.tasks.get(self.task_id)
        require_lease(task, self.token, "writer")
        active_story(work, task)
        context = ProductionContext.model_validate_json(task.input_json)
        return task, context, load_workspace(task, context)

    def get_revision_context(self) -> RevisionEntry:
        """读取当前候选正文和必改集合；账本用 read_revision_candidate 按关键词读取。"""
        with self.app.unit() as work:
            task, context, workspace = self._load(work)
            current = workspace.current(context.draft)
            for name in task.required_resources:
                read_skill(work, work.tasks.get(task.id), name)
            related = []
            if task.revision_window_policy:
                anchors = [
                    a for r in context.reader_reviews[-1:] for i in r.submission.issues for a in i.anchors
                ]
                related = [
                    IndexedClaim(index=i, value=c)
                    for i, c in enumerate(current.claims)
                    if any(a in c.claim or c.claim in a for a in anchors)
                ]
            return RevisionEntry(
                related_claims=related[:10],
                draft_id=context.draft.id,
                base_content_hash=context.draft.content_hash,
                version=workspace.version,
                candidate_hash=candidate_hash(current),
                brief=context.story.brief,
                title=current.title,
                body=current.body[:6000],
                body_characters=len(current.body),
                truncated=len(current.body) > 6000,
                issues=resolve_active_issue_set(
                    context.reviews,
                    context.reader_reviews,
                    current_reports=task.revision_window_policy is not None,
                ),
                resources=[task.newsroom_skills.read(name) for name in task.required_resources],
                optional_resources=[
                    r.name
                    for r in task.newsroom_skills.resources
                    if r.name.startswith("standards/") and r.name not in task.required_resources
                ],
                next_action=(
                    "复用已保存候选；按问题读引用和必要证据，edit_revision_candidate小步保存，"
                    "respond_revision_issue回应，validate_revision_candidate预检，"
                    "submit_revision_candidate正式提交；候选不是审核通过。"
                ),
            )

    def read_revision_candidate(
        self,
        section: Literal["body", "claims", "diff", "metadata", "history", "issues"] = "diff",
        start: int = 0,
        query: str = "",
    ) -> CandidateRead:
        """body/diff每次6000字符，claims每次10条；query只筛选包含原文关键词的段落/主张。"""
        if start < 0 or len(query) > 500:
            raise ValueError("读取窗口或关键词无效")
        with self.app.unit() as work:
            _, context, workspace = self._load(work)
            current = workspace.current(context.draft)
            if section == "claims":
                rows = [
                    IndexedClaim(index=i, value=c)
                    for i, c in enumerate(current.claims)
                    if not query or query.casefold() in c.claim.casefold()
                ]
                end = start + 10
                return CandidateRead(
                    version=workspace.version,
                    section=section,
                    claims=rows[start:end],
                    total=len(rows),
                    next_start=end if end < len(rows) else None,
                )
            if section == "diff":
                text = "".join(
                    difflib.unified_diff(
                        context.draft.submission.body.splitlines(keepends=True),
                        current.body.splitlines(keepends=True),
                        fromfile="base",
                        tofile="candidate",
                    )
                )
            elif section == "metadata":
                text = current.model_dump_json(exclude={"body", "claims"})
            elif section == "history":
                text = "\n".join(v.model_dump_json(exclude={"submission"}) for v in workspace.versions)
            elif section == "issues":
                # 保留准确报告与稿件身份，默认不再展开历史问题长文；query可以定位原ID。
                text = "\n".join(
                    f"{kind} report={r.id} draft={r.draft_id} hash={r.draft_hash} "
                    f"decision={r.submission.decision} "
                    f"open={','.join(i.id for i in r.submission.issues)} "
                    f"closed={','.join(getattr(r.submission, 'closed_issue_ids', []))}"
                    for kind, reports in (("fact", context.reviews), ("reader", context.reader_reviews))
                    for r in reports
                )
            else:
                text = current.body
            if query:
                parts = text.splitlines() if section in {"issues", "history"} else text.split("\n\n")
                text = "\n\n".join(p for p in parts if query.casefold() in p.casefold())
            end = start + 6000
            return CandidateRead(
                version=workspace.version,
                section=section,
                text=text[start:end],
                total=len(text),
                next_start=end if end < len(text) else None,
            )

    def _save(
        self,
        work: ProductionUnit,
        task: ProductionTask,
        workspace: RevisionWorkspace,
        current: DraftSubmission,
        reason: str,
        expected_version: int,
    ) -> CandidateReceipt:
        if expected_version != workspace.version:
            raise ValueError("候选版本已变化，请重新读取后编辑")
        if len(workspace.versions) >= 50:
            raise ValueError("候选保存次数达到50次，请提交或报告阻断；不覆盖已有保存点")
        version = CandidateVersion(
            version=workspace.version + 1,
            origin_task_id=task.id,
            created_at=utcnow(),
            reason=reason,
            submission=current,
        )
        saved = workspace.model_copy(update={"versions": [*workspace.versions, version]})
        work.tasks.save(
            task.model_copy(
                update={"revision_workspace_json": saved.model_dump_json(), "updated_at": utcnow()}
            )
        )
        return CandidateReceipt(
            version=saved.version,
            state="saved",
            next_action="候选已保存；查看diff、补齐引用与问题回应后预检，尚未正式交稿。",
        )

    def edit_revision_candidate(self, edit: CandidateEdit, expected_version: int) -> CandidateReceipt:
        """将最多五处替换保存到当前候选；错误返回可修正反馈，不覆盖已有候选。"""
        with self.app.unit() as work:
            task, context, workspace = self._load(work)
            if expected_version != workspace.version:
                raise ValueError("候选版本已变化，请重新读取后编辑")
            try:
                current = edit_candidate(context.draft, workspace.current(context.draft), edit)
            except ValueError as exc:
                return CandidateReceipt(
                    version=workspace.version,
                    state="invalid",
                    feedback=[CandidateFeedback(code="PATCH_INVALID", message=str(exc))],
                    next_action="读取当前候选准确原文并修正补丁；本次未保存。",
                )
            return self._save(work, task, workspace, current, edit.reason, expected_version)

    def apply_revision_step(self, step: CandidateStep, expected_version: int) -> CandidateReceipt:
        """一次保存局部补丁与回应并返回正式校验反馈；预检失败仍保留候选，提交独立执行。"""
        with self.app.unit() as work:
            task, context, workspace = self._load(work)
            if task.revision_window_policy is None:
                raise ValueError("冻结任务未启用合并返修步骤")
            if expected_version != workspace.version:
                raise ValueError("候选版本已变化，请重新读取后编辑")
            issues = resolve_active_issue_set(
                context.reviews,
                context.reader_reviews,
                current_reports=task.revision_window_policy is not None,
            )
            ids = [r.issue_id for r in step.responses]
            if len(ids) != len(set(ids)) or set(ids) - set(issues.required_ids):
                raise ValueError("回应必须唯一且属于当前必改问题")
            try:
                current = edit_candidate(context.draft, workspace.current(context.draft), step.edit)
            except ValueError as exc:
                return CandidateReceipt(
                    version=workspace.version,
                    state="invalid",
                    feedback=[CandidateFeedback(code="PATCH_INVALID", message=str(exc))],
                    next_action="未保存；按错误修正准确补丁。",
                )
            current = current.model_copy(
                update={
                    "resolved_issue_ids": sorted(set(current.resolved_issue_ids) | set(ids)),
                    "reader_responses": [r for r in current.reader_responses if r.issue_id not in ids]
                    + [r for r in step.responses if r.issue_id in issues.known_reader_ids],
                }
            )
            receipt = self._save(work, task, workspace, current, step.edit.reason, expected_version)
            try:
                require_skill_reads(task)
                validate_submission(work, task, context, current)
            except ValueError as exc:
                return receipt.model_copy(
                    update={
                        "feedback": [CandidateFeedback(code="SUBMISSION_INVALID", message=str(exc))],
                        "next_action": "候选已保存，按反馈修正；不要重复提交原补丁。",
                    }
                )
            return receipt.model_copy(
                update={
                    "state": "ready_for_review",
                    "next_action": "已保存且机械校验通过；直接submit_revision_candidate，不必重复预检。",
                }
            )

    def respond_revision_issue(
        self,
        issue_id: str,
        action: Literal["revised", "retained"],
        reason: str,
        expected_version: int,
    ) -> CandidateReceipt:
        """逐项保存处理依据；回应不表示问题被独立审核关闭。"""
        response = ReaderRevisionResponse(issue_id=issue_id, action=action, reason=reason)
        with self.app.unit() as work:
            task, context, workspace = self._load(work)
            issues = resolve_active_issue_set(
                context.reviews,
                context.reader_reviews,
                current_reports=task.revision_window_policy is not None,
            )
            if issue_id not in issues.required_ids:
                raise ValueError("仅回应当前必改问题，不能重开已关闭问题")
            current = workspace.current(context.draft)
            responses = [r for r in current.reader_responses if r.issue_id != issue_id]
            if issue_id in issues.known_reader_ids:
                responses.append(response)
            current = current.model_copy(
                update={
                    "resolved_issue_ids": sorted(set(current.resolved_issue_ids) | {issue_id}),
                    "reader_responses": responses,
                    "coverage": "逐项返修说明：" + reason,
                }
            )
            return self._save(work, task, workspace, current, f"{issue_id}: {reason}", expected_version)

    def restore_revision_candidate(self, version: int, expected_version: int) -> CandidateReceipt:
        """恢复明确保存点并追加新版本；version=0恢复原稿，不删除历史。"""
        with self.app.unit() as work:
            task, context, workspace = self._load(work)
            if version == 0:
                current = workspace.model_copy(update={"versions": []}).current(context.draft)
            else:
                found = next((v for v in workspace.versions if v.version == version), None)
                if found is None:
                    raise ValueError("候选保存点不存在")
                current = found.submission
            return self._save(work, task, workspace, current, f"恢复候选版本{version}", expected_version)

    def validate_revision_candidate(self, expected_version: int) -> CandidateReceipt:
        """执行与交稿相同的机械预检；失败保留候选，成功仍需正式提交和独立审核。"""
        with self.app.unit() as work:
            task, context, workspace = self._load(work)
            current = committed_candidate(task, context, expected_version)
            try:
                require_skill_reads(task)
                validate_submission(work, task, context, current)
            except ValueError as exc:
                return CandidateReceipt(
                    version=workspace.version,
                    state="invalid",
                    feedback=[CandidateFeedback(code="SUBMISSION_INVALID", message=str(exc))],
                    next_action="按反馈修正候选后重试预检；已有候选仍可恢复。",
                )
            return CandidateReceipt(
                version=workspace.version,
                state="ready_for_review",
                next_action="机械预检通过；submit_revision_candidate提交后仍需独立阅读与事实核验。",
            )

    def submit_revision_candidate(self, expected_version: int) -> str:
        """提交准确已保存候选；原子复验、生成正式稿并进入独立审核。"""
        return self.app.handoff.content(
            self.task_id, self.token, CandidateCommit(expected_version=expected_version)
        )
