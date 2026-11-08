"""制作角色的执行身份、额度与租约契约，不拥有稿件业务规则。"""

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SerializerFunctionWrapHandler, model_serializer

from agent4pharma.runtime.context_window_policy import RevisionWindowPolicy
from agent4pharma.runtime.skill_resources import SkillBundle

# reviewer 是角色配置键，reading-review 和 fact-check 分别决定评审任务的职责。
# 阶段标识用于持久化和冻结技能匹配，展示名称由工作台提供。
ProductionRole = Literal["editor", "writer", "reviewer", "publisher"]
ProductionPhase = Literal[
    "",
    "topic-selection",
    "draft-writing",
    "fact-check",
    "reading-review",
    "layout-production",
    "article-revision",
]


class ProductionPreparationPending(ValueError):
    """可直接展示的文档准备阻断，不携带底层解析诊断或凭证。"""

    def __init__(self) -> None:
        self.public_message = (
            "PDF解析缓存尚未就绪，当前任务尚未调用模型；请查看文档准备状态，准备完成后显式执行。"
        )
        super().__init__(self.public_message)


class ProductionLimits(BaseModel):
    """每个角色任务的硬上限；调用授权由入口显式提交，不从页面读取。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    max_attempts: int = Field(default=2, ge=1, le=5)
    max_tokens: int = Field(default=4096, ge=256, le=16384)
    timeout_seconds: int = Field(default=180, ge=10, le=1000)
    max_reads: int = Field(default=20, ge=1, le=50)
    reasoning_effort: Literal["off", "low", "high", "max"] = "high"

    max_revision_rounds: int | None = Field(default=None, ge=1, le=10)

    @model_serializer(mode="wrap")
    def serialize_limits(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        """旧额度未声明总返修预算时不补字段，保持冻结身份与授权不变。"""
        result = handler(self)
        if self.max_revision_rounds is None:
            result.pop("max_revision_rounds", None)
        return result

    @classmethod
    def for_new_task(cls) -> "ProductionLimits":
        """新制作任务采用可完成长任务书的预算；旧快照省略字段的语义不变。"""
        return cls(max_tokens=16384, timeout_seconds=1000, reasoning_effort="low", max_revision_rounds=5)


class ProductionTask(BaseModel):
    """持久化制作任务；输入为已校验上下文序列化，模型不能改身份和额度。"""

    id: str
    role: ProductionRole
    story_id: str = ""
    expected_revision: int = 0
    status: Literal["queued", "running", "succeeded", "failed", "blocked", "paused", "cancelled"] = "queued"
    actor_id: str
    model: str
    endpoint: str
    prompt_hash: str
    prompt: str
    input_json: str
    limits: ProductionLimits
    attempt: int = 0
    lease_token: str = ""
    lease_until: str = ""
    reads: int = 0
    figure_queries: tuple[str, ...] = ()
    figure_pages: tuple[str, ...] = ()
    figure_inspections: tuple[str, ...] = ()
    citation_lookups: tuple[str, ...] = ()
    result_ref: str = ""
    error: str = ""
    created_at: str
    updated_at: str
    newsroom_skills: SkillBundle | None = None
    phase: ProductionPhase = ""
    newsroom_policy: str = ""
    web_search_enabled: bool = False
    research_policy: Literal["", "pitch-first-v1"] = ""
    document_reads: dict[str, str] = Field(default_factory=dict)
    parent_task_id: str = ""
    required_resources: tuple[str, ...] = ()
    skill_reads: tuple[str, ...] = ()
    supplemental_evidence_json: str = ""
    editing_result_json: str = ""
    writing_research_json: str = ""
    background_research_json: str = ""
    reader_observation_json: str = ""
    reader_observation_created_at: str = ""
    reader_brief_read: bool = False
    auto_run: bool = False
    binary_editorial_decision: bool = False
    context_contract: Literal["", "node-context-v1", "node-context-v2"] = ""
    context_tools: tuple[str, ...] = ()
    revision_workspace_json: str = ""
    revision_window_policy: RevisionWindowPolicy | None = None

    @model_serializer(mode="wrap")
    def serialize_task(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        """旧任务不补空技能字段，保持历史快照语义；新任务完整保存技能。"""
        result = handler(self)
        if self.newsroom_skills is None:
            result.pop("newsroom_skills", None)
        for key in (
            "phase",
            "figure_queries",
            "figure_pages",
            "figure_inspections",
            "citation_lookups",
            "newsroom_policy",
            "web_search_enabled",
            "research_policy",
            "document_reads",
            "parent_task_id",
            "required_resources",
            "skill_reads",
            "supplemental_evidence_json",
            "editing_result_json",
            "writing_research_json",
            "background_research_json",
            "auto_run",
            "binary_editorial_decision",
            "reader_observation_json",
            "reader_observation_created_at",
            "reader_brief_read",
            "context_contract",
            "context_tools",
            "revision_workspace_json",
            "revision_window_policy",
        ):
            if not getattr(self, key):
                result.pop(key, None)
        return result


class ProductionRunner(Protocol):
    """仅执行已领取任务；在事务之外启动 SDK，完成必须经工具提交。"""

    def run(self, task: ProductionTask) -> None:
        """有界调用模型；最终自由文本不是业务结果。"""
        ...
