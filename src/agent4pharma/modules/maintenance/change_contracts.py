"""来源维护的结构化变更草案，模型只能建议，人工确认后由程序发布。"""

from typing import Literal, Protocol

from pydantic import BaseModel, Field, model_validator

from agent4pharma.modules.discovery.intelligence_contracts import IntelligencePolicy
from agent4pharma.modules.discovery.screening_policy import ScreeningPolicy
from agent4pharma.modules.sources.management import SourceEdit


class MaintenanceChange(BaseModel):
    """变更必须绑定实际任务证据和当前版本，不允许自由 SQL 或任意字段写入。"""

    kind: Literal["source_follow", "venue_watch", "coarse_policy", "intelligence_policy"]
    reason: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)
    target_id: str = ""
    expected_version: str = ""
    source: SourceEdit | None = None
    venue_name: str = Field(default="", max_length=300)
    venue_url: str = Field(default="", max_length=2000)
    coarse: ScreeningPolicy | None = None
    intelligence: IntelligencePolicy | None = None
    risks: str = Field(default="", max_length=4000)
    regression_task_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_kind(self) -> "MaintenanceChange":
        """每种变更只有一种业务对象，避免隐藏额外修改。"""
        expected = {
            "source_follow": "source",
            "coarse_policy": "coarse",
            "intelligence_policy": "intelligence",
        }
        for kind, field in expected.items():
            if (getattr(self, field) is not None) != (self.kind == kind):
                raise ValueError("变更内容与类型不一致")
        if self.kind == "venue_watch" and not self.venue_name.strip():
            raise ValueError("期刊建议需要实际名称")
        return self


class ChangeProposal(BaseModel):
    """待确认建议与执行状态，重复确认返回同一结果。"""

    id: str
    job_id: str
    status: str
    change: MaintenanceChange
    resolution: str = ""
    created_at: str


class MaintenanceChanges(Protocol):
    """来源维护专用存取能力，不授予模型确认权限。"""

    def propose(self, job_id: str, change: MaintenanceChange) -> ChangeProposal:
        """校验证据与版本后保存建议。"""
        ...

    def list_proposals(self, job_id: str | None = None) -> list[ChangeProposal]:
        """读取待确认项和历史状态。"""
        ...

    def resolve(self, proposal_id: str, accept: bool) -> ChangeProposal:
        """仅人工入口可调用的确认操作，返回幂等结果。"""
        ...
