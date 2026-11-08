"""返修展示和提交共用的有效问题集合；保持现有必改规则与准确报告来源。"""

import hashlib

from pydantic import BaseModel, Field

from agent4pharma.modules.review.reader_review_contracts import ReaderIssue, ReaderReviewReport
from agent4pharma.modules.review.review_contracts import ReviewIssue, ReviewReport


class IssueOrigin(BaseModel):
    """问题身份属于准确报告和稿件，同名ID不隐式认定为同一事实问题。"""

    issue_id: str
    report_id: str
    draft_id: str
    draft_hash: str
    kind: str


class ActiveIssueSet(BaseModel):
    """报告身份进入范围 hash，同名问题不用于推断跨轮关闭。"""

    fact_issues: list[ReviewIssue]
    reader_issues: list[ReaderIssue]
    known_reader_ids: list[str]
    closed_reader_ids: list[str]
    required_ids: list[str]
    report_refs: list[str]
    scope_hash: str
    origins: list[IssueOrigin] = Field(default_factory=list)


def resolve_active_issue_set(
    reviews: list[ReviewReport], readers: list[ReaderReviewReport], *, current_reports: bool = False
) -> ActiveIssueSet:
    """事实沿现行全必改、阅读沿最近报告；作者回应不等同关闭。"""
    relevant_facts = reviews[-1:] if current_reports else reviews
    relevant_readers = readers[-1:] if current_reports else readers
    facts = {i.id: i for r in relevant_facts for i in r.submission.issues if i.severity != "suggestion"}
    reading = [i for r in readers[-1:] for i in r.submission.issues if i.severity == "required"]
    if current_reports and set(facts) & {i.id for i in reading}:
        raise ValueError("事实与阅读报告的问题ID冲突，须明确报告身份后处理，不能合并回应")
    reports = [*relevant_facts, *relevant_readers]
    refs = [r.id for r in reports]
    digest = hashlib.sha256("|".join(r.model_dump_json() for r in reports).encode()).hexdigest()
    closed = set()
    for report in readers:
        closed.update(report.submission.closed_issue_ids)
        if current_reports:
            closed.difference_update(i.id for i in report.submission.issues)
    return ActiveIssueSet(
        fact_issues=list(facts.values()),
        reader_issues=reading,
        required_ids=sorted(set(facts) | {i.id for i in reading}),
        report_refs=refs,
        scope_hash=digest,
        known_reader_ids=sorted({i.id for r in readers for i in r.submission.issues}),
        closed_reader_ids=sorted(closed),
        origins=[
            IssueOrigin(
                issue_id=i.id, report_id=r.id, draft_id=r.draft_id, draft_hash=r.draft_hash, kind=kind
            )
            for kind, group in (("fact", relevant_facts), ("reader", readers[-1:]))
            for r in group
            for i in r.submission.issues
            if i.severity != "suggestion"
        ]
        if current_reports
        else [],
    )
