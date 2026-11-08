"""阅读品质报告：定位理解障碍，不认证事实或预测传播数据。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReaderObservation(BaseModel):
    """只读成稿后的阅读结果；先保存，再开放任务书，避免底稿替正文补义。"""

    model_config = ConfigDict(extra="forbid")
    news_understood: str = Field(min_length=10, max_length=800)
    reason_to_read: str = Field(min_length=5, max_length=500)
    first_obstacle: str = Field(min_length=2, max_length=1000)


class ReaderIssue(BaseModel):
    """具体阅读缺陷及影响；锚点可跨段，不按词命中判错。"""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    dimension: Literal["appeal_and_flow", "language_naturalness"]
    severity: Literal["required", "suggestion"]
    anchors: list[str] = Field(min_length=1, max_length=8)
    problem: str = Field(min_length=10, max_length=1500)
    reader_impact: str = Field(min_length=10, max_length=1000)
    resolution: str = Field(min_length=10, max_length=1500)
    example_revision: str = Field(default="", max_length=1500)


class ReaderReviewSubmission(BaseModel):
    """两项评审均完成才可判阅读就绪；不接受综合自评分替代问题定位。"""

    model_config = ConfigDict(extra="forbid")
    decision: Literal["ready", "changes_requested", "rebrief_needed"]
    summary: str = Field(min_length=10, max_length=3000)
    appeal_and_flow: Literal["completed", "incomplete"]
    language_naturalness: Literal["completed", "incomplete"]
    issues: list[ReaderIssue] = Field(default_factory=list, max_length=20)
    closed_issue_ids: list[str] = Field(default_factory=list, max_length=20)


class ReaderReviewReport(BaseModel):
    """不可变阅读报告，绑定准确稿件、读者规范与评审技能快照。"""

    id: str
    story_id: str
    draft_id: str
    draft_hash: str
    brief_version: int
    reviewer_id: str
    audience_hash: str
    rubric_hash: str
    observation: ReaderObservation
    submission: ReaderReviewSubmission
    created_at: str


class ReaderRevisionIssue(ReaderIssue):
    """本次阅读评审中必须交作者处理的问题。"""

    severity: Literal["required"]


class ReaderReviewResult(ReaderReviewSubmission):
    """新提交只允许通过或要求返修；历史报告仍可读取旧的复议结论。"""

    decision: Literal["ready", "changes_requested"]
    issues: list[ReaderRevisionIssue] = Field(default_factory=list, max_length=20)


def validate_reader_review(result: ReaderReviewSubmission, text: str, previous_required: set[str]) -> None:
    """校验完成性、准确锚点与复核闭环；语义合理性仍由编辑负责。"""
    if result.decision not in {"ready", "changes_requested"}:
        raise ValueError("阅读评审只能通过或要求返修，选题疑问须给出具体修改要求")
    if result.appeal_and_flow != "completed" or result.language_naturalness != "completed":
        raise ValueError("阅读评审未完成，不能提交业务结论；技术失败须明确报告")
    ids = [issue.id for issue in result.issues]
    if len(set(ids)) != len(ids) or len(set(result.closed_issue_ids)) != len(result.closed_issue_ids):
        raise ValueError("阅读问题ID或关闭ID重复")
    if set(ids) & set(result.closed_issue_ids):
        raise ValueError("同一问题不能同时打开与关闭")
    if set(result.closed_issue_ids) - previous_required:
        raise ValueError("只能关闭前轮实际存在的必改问题")
    if previous_required - set(result.closed_issue_ids) - set(ids):
        raise ValueError("复核须关闭或沿原ID保留前轮全部必改问题")
    for issue in result.issues:
        if any(not anchor.strip() or anchor not in text for anchor in issue.anchors):
            raise ValueError(f"{issue.id}: 阅读问题须引用当前标题或正文中准确存在的文字")
    if any(issue.severity == "suggestion" for issue in result.issues):
        raise ValueError("问题单只收录必须返修的问题，阅读评审通过时须提交空问题单")
    if result.decision != ("changes_requested" if result.issues else "ready"):
        raise ValueError("阅读结论与必改问题不一致")
