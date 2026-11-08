"""主编决定与不可变任务书，候选引用不代替正式立项。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CommissionBrief(BaseModel):
    """唯一报道角度及其关键事实边界。"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    angle: str = Field(min_length=1, max_length=240)
    boundary: str = Field(default="", max_length=600)
    title: str = Field(default="", max_length=100)


class _DecisionDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    candidate_ids: list[str] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=10, max_length=3000)
    brief: CommissionBrief | None = None


class EditorialDecisionSubmission(_DecisionDetails):
    """主编提交报道或不报道；合题在 candidate_ids 中明确表达。"""

    decision: Literal["commission", "decline"]


class EditorialDecision(_DecisionDetails):
    """持久化的编辑决定，兼容历史决定和冻结任务的提交。"""

    decision: Literal["commission", "decline", "defer", "request_evidence"]
    revisit_condition: str = Field(default="", max_length=2000)


class Story(BaseModel):
    """制作聚合的当前投影；旧任务书另存历史，不覆盖已交付版本。"""

    id: str
    revision: int = 1
    brief_version: int = 1
    brief: CommissionBrief
    editorial_reason: str = ""
    read_only: bool = Field(default=False, exclude_if=lambda value: not value)
    candidate_ids: list[str]
    status: Literal["active", "deferred", "cancelled", "completed"] = "active"
    created_at: str
    updated_at: str


class EditorialDecisionRecord(BaseModel):
    """一次已提交的编辑决定，保留任务与候选引用，不由任务状态猜测理由。"""

    task_id: str
    decision: EditorialDecision
    created_at: str = ""


def validate_decision(decision: EditorialDecision, allowed: set[str], *, binary: bool = False) -> None:
    """校验任务冻结的决定范围、候选引用与任务书；历史暂缓须有重新查看条件。"""
    if binary and (decision.decision not in {"commission", "decline"} or decision.revisit_condition):
        raise ValueError("主编只能提交报道或不报道及当前理由")
    if set(decision.candidate_ids) - allowed or len(set(decision.candidate_ids)) != len(
        decision.candidate_ids
    ):
        raise ValueError("候选不属于绑定任务或包含重复引用")
    if (decision.decision == "commission") != (decision.brief is not None):
        raise ValueError("只有立项决定必须携带任务书")
    if decision.decision in {"defer", "request_evidence"} and not decision.revisit_condition.strip():
        raise ValueError("延期或待补证必须说明重新查看条件")
