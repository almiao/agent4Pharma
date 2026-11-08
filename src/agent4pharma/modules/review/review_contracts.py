"""独立审阅的结论与问题；通过不等于对外发送授权。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReviewIssue(BaseModel):
    """可定位且有解决条件的问题；稳定 ID 支持作者逐项回复。"""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    severity: Literal["blocking", "required", "suggestion"]
    location: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=10, max_length=3000)
    resolution: str = Field(min_length=10, max_length=3000)
    category: Literal["legacy", "factual_error", "requirement_gap", "style"] = "legacy"
    draft_quote: str = Field(default="", max_length=2000, description="事实必改：从当前稿件复制准确问题句")
    evidence_id: str = ""
    source_quote: str = Field(default="", max_length=3000, description="事实必改：来源中连续存在的纠正依据")
    requirement: str = Field(
        default="", max_length=600, description="要求缺失：准确复制当前任务书的必答项或验收要求"
    )


class ReviewSubmission(BaseModel):
    """核验任务书、证据与表述的结构化判断，不执行科研真实性认证。"""

    model_config = ConfigDict(extra="forbid")
    decision: Literal["pass", "changes_requested", "blocked"]
    summary: str = Field(min_length=10, max_length=4000)
    issues: list[ReviewIssue] = Field(default_factory=list, max_length=100)


class ReviewReport(BaseModel):
    """绑定稿件 hash 与任务书版本的不可变审阅报告。"""

    id: str
    story_id: str
    draft_id: str
    draft_hash: str
    brief_version: int
    reviewer_id: str
    submission: ReviewSubmission
    created_at: str


class RevisionIssue(ReviewIssue):
    """本次核验中必须交作者处理的问题。"""

    severity: Literal["blocking", "required"]


class ReviewResult(ReviewSubmission):
    """新编辑核验结论只有通过或要求返修；严重问题仍须交作者处理。"""

    decision: Literal["pass", "changes_requested"]
    issues: list[RevisionIssue] = Field(default_factory=list, max_length=100)


def validate_review(result: ReviewSubmission, author_id: str, reviewer_id: str) -> None:
    """禁止自审和带必改项通过；意见必须一致且问题身份唯一。"""
    if author_id == reviewer_id:
        raise ValueError("作者不能审核自己的稿件")
    if len({issue.id for issue in result.issues}) != len(result.issues):
        raise ValueError("审核问题 ID 重复")
    if any(issue.severity == "suggestion" for issue in result.issues):
        raise ValueError("问题单只收录必须返修的问题，核验通过时须提交空问题单")
    expected = "changes_requested" if result.issues else "pass"
    if result.decision != expected:
        raise ValueError("审核结论与阻断/必改项不一致")
