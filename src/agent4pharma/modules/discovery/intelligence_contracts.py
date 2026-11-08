"""情报判定公开契约：同一评估供日常推荐、维护复测和自检复用。"""

from typing import Literal, Protocol

from pydantic import BaseModel, Field, JsonValue, SerializerFunctionWrapHandler, model_serializer

from agent4pharma.modules.materials.document_reading import DocumentContinuation, DocumentReference


class IntelligencePolicy(BaseModel):
    """可发布的语义判断标准，模型调用只能引用快照，不能自行改写。"""

    instructions: str = Field(min_length=40, max_length=24000)
    scope_version: str = "agent-llm-pharma-v1"
    examples: str = Field(default="", max_length=12000)
    decision_mode: Literal["legacy", "single_pass"] = "legacy"

    @model_serializer(mode="wrap")
    def serialize_policy(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        """旧准则省略新默认字段，维持历史请求内容身份和幂等语义。"""
        result = handler(self)
        if self.decision_mode == "legacy":
            result.pop("decision_mode", None)
        return result


class IntelligencePolicyVersion(BaseModel):
    """语义准则不可变版本，发布状态与内容 hash 分开保存。"""

    id: str
    version: int
    status: str
    definition: IntelligencePolicy
    content_hash: str


class IntelligenceEvidence(BaseModel):
    """实际可见资料；ID 和原文片段支持程序校验，手工正文保持出处。"""

    id: str
    title: str = ""
    text: str = Field(max_length=60000)
    url: str = ""
    origin: str = "metadata"
    status: str = "available"
    doi: str = ""
    abstract_status: Literal["found", "none", "error"] | None = None
    abstract_source: str = ""
    source_abstract: str = Field(default="", max_length=60000)
    content_hash: str = ""
    retrieved_at: str = ""
    document: DocumentReference | None = None
    read_scope: str = ""
    locators: list[str] = Field(default_factory=list)
    truncated: bool = False
    next_request: DocumentContinuation | None = None

    @model_serializer(mode="wrap")
    def serialize_evidence(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        """旧证据不追加空解析元数据。"""
        result = handler(self)
        for key in ("document", "read_scope", "locators", "truncated", "next_request"):
            if not result[key]:
                result.pop(key)
        return result


class IntelligenceRequest(BaseModel):
    """评估输入快照；replay/selfcheck 不写正式主编候选。"""

    material_ref: str
    mode: Literal["production", "replay", "selfcheck"] = "replay"
    evidence: list[IntelligenceEvidence] = Field(min_length=1, max_length=12)
    policy: IntelligencePolicyVersion
    model: str
    model_endpoint: str = "https://api.deepseek.com"
    model_max_tokens: int = 4096
    parent_ref: str = ""
    previous_assessment: str = ""
    history: list[str] = Field(default_factory=list, max_length=20)
    require_primary_read: bool = False
    stage: Literal["legacy", "abstract", "fulltext"] = "legacy"
    selection_ref: str = ""
    stage_instructions: str = ""
    abstract_checked: bool = False
    fulltext_parser: Literal["legacy", "mineru-standard-v1"] = "legacy"

    @model_serializer(mode="wrap")
    def serialize_request(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        """历史任务省略新默认值，保持原始序列化及任务 hash。"""
        result = handler(self)
        if self.stage == "legacy":
            result.pop("stage", None)
        if not self.selection_ref:
            result.pop("selection_ref", None)
        if not self.abstract_checked:
            result.pop("abstract_checked", None)
        if not self.stage_instructions:
            result.pop("stage_instructions", None)
        if self.fulltext_parser == "legacy":
            result.pop("fulltext_parser", None)
        return result


class IntelligenceAssessment(BaseModel):
    """主题、终判及排除原因分开；历史路由按绑定准则校验，推荐需真实领域证据。"""

    relevance: Literal["core", "industry_update", "related", "out_of_scope", "insufficient_information"]
    disposition: Literal["recommend", "exclude", "watch", "needs_evidence", "out_of_scope"]
    exclusion_reason: (
        Literal["insufficient_evidence", "out_of_scope", "low_editorial_value", "duplicate"] | None
    ) = None
    summary: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=4000)
    attention_reason: str = Field(default="", max_length=2000)
    evidence_ids: list[str] = Field(min_length=0, max_length=12)
    ai_quote: str = Field(default="", max_length=1200)
    domain_quote: str = Field(default="", max_length=1200)
    uncertainties: list[str] = Field(default_factory=list, max_length=20)
    policy_feedback: str = Field(default="", max_length=3000)
    priority_relevance: int | None = Field(default=None, ge=0, le=3)

    @model_serializer(mode="wrap")
    def serialize_assessment(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        """历史结果不添加空排除原因字段；新终判结果显式保留原因。"""
        result = handler(self)
        if self.exclusion_reason is None:
            result.pop("exclusion_reason", None)
        if self.priority_relevance is None:
            result.pop("priority_relevance", None)
        return result


class IntelligenceTask(BaseModel):
    """一次受限模型评估及结构化结果；失败和未运行不表示排除。"""

    id: str
    status: str
    request: IntelligenceRequest
    result: IntelligenceAssessment | None = None
    error: str = ""
    supplemental_evidence: list[IntelligenceEvidence] = Field(default_factory=list)


class IntelligenceTaskSummary(BaseModel):
    """管理列表的有界只读投影，不携带摘要、全文或隐藏模型输入。"""

    id: str
    status: str
    material_ref: str
    title: str
    mode: Literal["production", "replay", "selfcheck"]
    parent_ref: str
    updated_at: str
    summary: str = ""
    disposition: str = ""
    abstract_state: str = "unknown"
    fulltext_state: str = "unknown"
    source_record_id: str = ""
    current: bool = True
    error: str = ""
    stage: Literal["legacy", "abstract", "fulltext"] = "legacy"
    selection_ref: str = ""


class IntelligenceMaintenanceRequest(BaseModel):
    """情报发现判断标准/来源问题后的维护请求，不等于已批准变更。"""

    task_id: str
    reason: str
    created_at: str
    investigation_status: str


class IntelligenceStore(Protocol):
    """历史语义准则、评估与主编候选的只读契约。"""

    def policy(self, policy_id: str | None = None) -> IntelligencePolicyVersion:
        """取得指定或已发布准则。"""
        ...

    def versions(self) -> list[IntelligencePolicyVersion]:
        """读取准则历史用于人工审阅。"""
        ...

    def candidates(self, parent_ref: str = "") -> list[dict[str, JsonValue]]:
        """读取主编待选池。"""
        ...

    def maintenance_requests(self) -> list[IntelligenceMaintenanceRequest]:
        """读取情报主动反馈及后续调查状态。"""
        ...

    def get(self, task_id: str) -> IntelligenceTask:
        """读取评估。"""
        ...

    def recent_summaries(self, limit: int = 100) -> list[IntelligenceTaskSummary]:
        """按更新时间读取有界情报列表；正文仅在选择任务后读取。"""
        ...

    def run_summaries(self, parent_ref: str) -> list[IntelligenceTaskSummary]:
        """读取明确父运行的全部摘要及证据取得状态，不返回正文。"""
        ...

    def summaries_for_ids(self, task_ids: list[str]) -> list[IntelligenceTaskSummary]:
        """按已知引用读取有限任务摘要，支持历史选题绑定的评估版本。"""
        ...

    def tasks_for_parent(self, parent_ref: str) -> list[IntelligenceTask]:
        """读取绑定历史调查的评估记录。"""
        ...
