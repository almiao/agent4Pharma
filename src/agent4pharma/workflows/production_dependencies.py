"""制作应用的窄工作单元与角色上下文；不暴露连接或持久化实现。"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field, JsonValue, SerializerFunctionWrapHandler, model_serializer

from agent4pharma.modules.content.article_editing import ArticleEditRequest
from agent4pharma.modules.content.background_research import BackgroundResearchSnapshot
from agent4pharma.modules.content.draft_contracts import DraftRevision, ResearchEvidence
from agent4pharma.modules.content.writing_research import WritingResearchSnapshot
from agent4pharma.modules.editorial.commission_contracts import (
    EditorialDecision,
    EditorialDecisionRecord,
    Story,
)
from agent4pharma.modules.publishing.human_approval import HumanApproval
from agent4pharma.modules.publishing.release_contracts import ChannelPackage, ReleaseReceipt
from agent4pharma.modules.publishing.wechat_delivery import WechatDeliveryRecord
from agent4pharma.modules.review.reader_review_contracts import ReaderReviewReport
from agent4pharma.modules.review.review_contracts import ReviewReport
from agent4pharma.runtime.production_execution import ProductionPhase, ProductionTask


class DownloadSelectionBasis(BaseModel):
    """下载前比较的新闻线索及依据；不含流程记录或后续采编验收要求。"""

    news_hook: str
    reason: str
    evidence_quote: str = ""
    institution_quote: str = ""


class CandidateSnapshot(BaseModel):
    """候选公开契约的制作侧快照，不读写发现模块内部表。"""

    id: str
    assessment_id: str
    summary: str
    reason: str
    evidence: list[ResearchEvidence]
    selection_basis: DownloadSelectionBasis | None = None

    @model_serializer(mode="wrap")
    def serialize_snapshot(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        """旧候选不补默认字段，保持冻结输入及幂等身份。"""
        result = handler(self)
        if self.selection_basis is None:
            result.pop("selection_basis", None)
        return result


class ProductionContext(BaseModel):
    """角色只读的完整输入快照；不含其他选题或外部账号凭证。"""

    article_edit: ArticleEditRequest | None = None
    candidates: list[CandidateSnapshot] = Field(default_factory=list)
    story: Story | None = None
    evidence: list[ResearchEvidence] = Field(default_factory=list)
    draft: DraftRevision | None = None
    reviews: list[ReviewReport] = Field(default_factory=list)
    reader_reviews: list[ReaderReviewReport] = Field(default_factory=list)
    reader_policy: Literal["", "reader-quality-v1"] = ""
    review_mode: Literal["initial", "reconsideration"] = "initial"
    phase: ProductionPhase = ""
    parent_task_id: str = ""
    layout_source_package: str = ""
    writing_research: WritingResearchSnapshot | None = None
    background_research: list[BackgroundResearchSnapshot] = Field(default_factory=list)
    remake_request_id: str = ""
    remake_of_task_id: str = ""

    @model_serializer(mode="wrap")
    def serialize_context(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        """不为历史上下文添加默认字段，保持原始任务身份。"""
        result = handler(self)
        for key in (
            "phase",
            "reader_reviews",
            "reader_policy",
            "parent_task_id",
            "writing_research",
            "background_research",
            "layout_source_package",
            "article_edit",
            "remake_request_id",
            "remake_of_task_id",
        ):
            if not getattr(self, key):
                result.pop(key, None)
        return result


class StoryRepository(Protocol):
    """主编聚合存取契约。"""

    def get(self, identity: str) -> Story:
        """读取选题。"""
        ...

    def list(self) -> list[Story]:
        """列出选题。"""
        ...

    def save(self, story: Story, expected_revision: int) -> None:
        """乐观保存任务书。"""
        ...

    def decision(self, identity: str, decision: EditorialDecision) -> None:
        """保存编辑决定。"""
        ...

    def decisions(self) -> list[EditorialDecisionRecord]:
        """读取实际编辑决定及其来源任务。"""
        ...


class ContentRepository(Protocol):
    """稿件与实际证据端口。"""

    def list(self) -> list[DraftRevision]:
        """读取稿件版本。"""
        ...

    def get(self, identity: str) -> DraftRevision:
        """读取准确稿件。"""
        ...

    def save(self, draft: DraftRevision) -> None:
        """追加稿件。"""
        ...

    def evidence(self, story_id: str) -> list[ResearchEvidence]:
        """取得授权资料。"""
        ...

    def add_evidence(self, story_id: str, evidence: ResearchEvidence) -> None:
        """保存证据新版本。"""
        ...


class ReviewRepository(Protocol):
    """独立审阅报告端口。"""

    def list(self) -> list[ReviewReport]:
        """读取审阅记录。"""
        ...

    def save(self, report: ReviewReport) -> None:
        """追加准确版本结论。"""
        ...

    def reader_list(self) -> list[ReaderReviewReport]:
        """读取独立阅读品质报告。"""
        ...

    def save_reader(self, report: ReaderReviewReport) -> None:
        """追加准确稿件的阅读结论，不替代事实审核。"""
        ...


class PublishingRepository(Protocol):
    """渠道包与回执端口。"""

    def wechat_deliveries(self) -> list[WechatDeliveryRecord]:
        """读取真实草稿交付记录。"""
        ...

    def save_wechat_delivery(self, record: WechatDeliveryRecord) -> None:
        """同工作单元保存交付请求和进度，不独立提交。"""
        ...

    def approvals(self) -> list[HumanApproval]:
        """读取不可变人工定稿记录。"""
        ...

    def approve(self, approval: HumanApproval) -> None:
        """保存准确渠道包的人工批准，不提交事务。"""
        ...

    def packages(self) -> list[ChannelPackage]:
        """读取渠道包。"""
        ...

    def receipts(self) -> list[ReleaseReceipt]:
        """读取回执。"""
        ...

    def save(self, package: ChannelPackage) -> None:
        """保存本地产物。"""
        ...

    def receipt(self, receipt: ReleaseReceipt) -> None:
        """保存真实发行结果。"""
        ...


class TaskRepository(Protocol):
    """制作任务和操作记录端口。"""

    def list(self) -> list[ProductionTask]:
        """读取执行队列。"""
        ...

    def get(self, identity: str) -> ProductionTask:
        """读取绑定任务。"""
        ...

    def save(self, task: ProductionTask) -> None:
        """保存执行状态。"""
        ...

    def event(self, identity: str, story_id: str, actor: str, action: str, reason: str, now: str) -> None:
        """记录操作及原因。"""
        ...


class ProductionUnit(Protocol):
    """一个原子制作工作单元；事务由注入的上下文管理器持有。"""

    stories: StoryRepository
    drafts: ContentRepository
    reviews: ReviewRepository
    releases: PublishingRepository
    tasks: TaskRepository
