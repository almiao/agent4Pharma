"""返修候选的版本、局部补丁和恢复契约；候选不等于正式稿件或审核通过。"""

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent4pharma.modules.content.draft_contracts import (
    ClaimEvidence,
    DraftRevision,
    DraftSubmission,
    ReaderRevisionResponse,
)
from agent4pharma.modules.content.draft_revision import (
    DraftRevisionSubmission,
    RevisionChange,
    apply_draft_revision,
)


class CandidateEdit(BaseModel):
    """针对当前候选的小步补丁；引用索引以最近一次读取的候选为准。"""

    model_config = ConfigDict(extra="forbid")
    changes: list[RevisionChange] = Field(default_factory=list, max_length=5)
    added_claims: list[ClaimEvidence] = Field(default_factory=list, max_length=10)
    removed_claim_indices: list[int] = Field(default_factory=list, max_length=10)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    summary: str | None = Field(default=None, min_length=1, max_length=3000)
    limitations: list[str] | None = Field(default=None, max_length=20)
    reason: str = Field(min_length=5, max_length=1000)


class CandidateDraft(DraftSubmission):
    """编辑中允许引用暂缺或正文不足交稿长度，正式交稿须重新验证严格稿件结构。"""

    body: str = Field(min_length=1, max_length=60000)
    claims: list[ClaimEvidence] = Field(default_factory=list, max_length=100)


class CandidateStep(BaseModel):
    """一个问题的局部编辑和作者回应一次保存；机械反馈不自动提交或关闭问题。"""

    model_config = ConfigDict(extra="forbid")
    edit: CandidateEdit
    responses: list[ReaderRevisionResponse] = Field(default_factory=list, max_length=10)


class CandidateVersion(BaseModel):
    """工具明确保存的不可变候选版本，允许恢复但不替代正式交稿。"""

    version: int
    origin_task_id: str
    created_at: str
    reason: str
    submission: CandidateDraft


class RevisionWorkspace(BaseModel):
    """绑定准确原稿和问题集合的有界候选历史；仅技术重试可继承。"""

    base_draft_id: str
    base_content_hash: str
    issue_scope_hash: str
    versions: list[CandidateVersion] = Field(default_factory=list, max_length=50)

    @property
    def version(self) -> int:
        """零表示尚无工具保存的候选，读取原稿不产生保存点。"""
        return self.versions[-1].version if self.versions else 0

    def current(self, base: DraftRevision) -> CandidateDraft:
        """返回当前候选，未编辑时返回清空旧回应的原稿副本。"""
        if self.versions:
            return self.versions[-1].submission.model_copy(deep=True)
        return CandidateDraft.model_validate(
            {**base.submission.model_dump(), "resolved_issue_ids": [], "reader_responses": []}
        )


class CandidateCommit(BaseModel):
    """提交准确候选游标；宿主在同一事务内重新验证并生成正式稿。"""

    expected_version: int = Field(ge=1)


class CandidateFeedback(BaseModel):
    """机械校验反馈不表示事实含义或阅读质量通过。"""

    code: str
    message: str


class CandidateReceipt(BaseModel):
    """小步保存/校验回执；不回传整稿，不自动排队后续阶段。"""

    version: int
    state: Literal["saved", "invalid", "ready_for_review"]
    feedback: list[CandidateFeedback] = Field(default_factory=list)
    next_action: str


def edit_candidate(base: DraftRevision, current: DraftSubmission, edit: CandidateEdit) -> DraftSubmission:
    """复用正式补丁定位与证据继承规则，编辑只作用于候选副本。"""
    if not (edit.changes or edit.added_claims or edit.removed_claim_indices) and all(
        value is None for value in (edit.title, edit.summary, edit.limitations)
    ):
        raise ValueError("候选编辑必须包含实质修改")
    temporary = base.model_copy(update={"submission": current})
    patch = DraftRevisionSubmission(
        draft_id=base.id,
        content_hash=base.content_hash,
        **edit.model_dump(exclude={"reason"}),
        coverage="局部返修候选；正式提交前逐项回应并校验。",
        resolved_issue_ids=current.resolved_issue_ids,
        reader_responses=current.reader_responses,
    )
    result = apply_draft_revision(temporary, patch, submission_type=CandidateDraft)
    if result.model_dump(exclude={"coverage"}) == current.model_dump(exclude={"coverage"}):
        raise ValueError("候选内容未变化，请复用当前版本")
    return result


def candidate_hash(submission: DraftSubmission) -> str:
    """候选内容指纹仅用于差异和审计，不授予交稿或审核权限。"""
    return hashlib.sha256(submission.model_dump_json().encode()).hexdigest()
