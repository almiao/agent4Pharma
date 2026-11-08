"""自动返修的准确版本补丁；宿主合成整稿，沿用未变主张的原始证据。"""

from pydantic import BaseModel, ConfigDict, Field

from agent4pharma.modules.content.draft_contracts import (
    ClaimEvidence,
    DraftRevision,
    DraftSubmission,
    ReaderRevisionResponse,
)


class RevisionChange(BaseModel):
    """以准确且唯一的原文定位修改，重复文字须扩大上下文消除歧义。"""

    model_config = ConfigDict(extra="forbid")
    original: str = Field(min_length=1, max_length=12000)
    replacement: str = Field(max_length=12000)


class DraftRevisionSubmission(BaseModel):
    """仅提交变化及问题回应；null字段沿用旧值，空列表可明确清空限制。"""

    model_config = ConfigDict(extra="forbid")
    draft_id: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    changes: list[RevisionChange] = Field(default_factory=list, max_length=20)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    summary: str | None = Field(default=None, min_length=1, max_length=3000)
    limitations: list[str] | None = Field(default=None, max_length=20)
    added_claims: list[ClaimEvidence] = Field(default_factory=list, max_length=100)
    removed_claim_indices: list[int] = Field(default_factory=list, max_length=100)
    coverage: str = Field(min_length=10, max_length=4000)
    resolved_issue_ids: list[str] = Field(default_factory=list, max_length=100)
    reader_responses: list[ReaderRevisionResponse] = Field(default_factory=list, max_length=20)


def apply_draft_revision(
    base: DraftRevision,
    revision: DraftRevisionSubmission,
    *,
    submission_type: type[DraftSubmission] = DraftSubmission,
) -> DraftSubmission:
    """拒绝过期/歧义/重叠补丁；候选可使用宽松结构，正式交稿始终重新校验DraftSubmission。"""
    if (revision.draft_id, revision.content_hash) != (base.id, base.content_hash):
        raise ValueError("返修必须绑定当前准确稿件ID与content_hash")
    body = base.submission.body
    ranges = []
    for change in revision.changes:
        start = body.find(change.original)
        if start < 0 or body.find(change.original, start + 1) >= 0:
            raise ValueError("修改原文不存在或不唯一，请复制足够的原文上下文")
        ranges.append((start, start + len(change.original), change.replacement))
    ranges.sort()
    if any(left[1] > right[0] for left, right in zip(ranges, ranges[1:])):
        raise ValueError("修改范围不能重复或重叠")
    for start, end, replacement in reversed(ranges):
        body = body[:start] + replacement + body[end:]
    removed = revision.removed_claim_indices
    if len(set(removed)) != len(removed) or any(i < 0 or i >= len(base.submission.claims) for i in removed):
        raise ValueError("移除引用须使用当前稿件的有效零起始索引且不能重复")
    title = revision.title if revision.title is not None else base.submission.title
    for index, claim in enumerate(revision.added_claims):
        if claim.claim not in body:
            raise ValueError(
                f"added_claims[{index}].claim 须从修改后正文逐字复制，优先复制replacement中的原句"
            )
    text = body
    claims = [c for i, c in enumerate(base.submission.claims) if i not in removed and c.claim in text]
    claims.extend(revision.added_claims)
    values = base.submission.model_dump()
    values.update(
        body=body,
        title=title,
        claims=claims,
        coverage=revision.coverage,
        resolved_issue_ids=revision.resolved_issue_ids,
        reader_responses=revision.reader_responses,
    )
    for name in ("summary", "limitations"):
        value = getattr(revision, name)
        if value is not None:
            values[name] = value
    return submission_type.model_validate(values)
