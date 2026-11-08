"""人工文章调查的具名输入输出；原始证据、模型结论与人工修正分别保存。"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from agent4pharma.modules.discovery.intelligence_contracts import IntelligencePolicyVersion, IntelligenceTask
from agent4pharma.modules.discovery.screening_policy import GateResult, PolicyVersion
from agent4pharma.shared.url_validation import public_reference_url


class ArticleCaseInput(BaseModel):
    """人工提交内容与期望；URL/DOI 等身份和粘贴正文可分别提供。"""

    reference: str = Field(min_length=1, max_length=2000)
    title: str = Field(default="", max_length=1000)
    text: str = Field(default="", max_length=60000)
    expected: Literal["include", "exclude", "uncertain"] = "uncertain"
    reason: str = Field(default="", max_length=4000)
    manual_original_url: str = Field(default="", max_length=2000)
    revision: int | None = None

    @field_validator("reference", "manual_original_url")
    @classmethod
    def validate_reference(cls, value: str) -> str:
        """永久链接不能含凭证；标题及标准 ID 仍可直接输入。"""
        if value.lower().startswith(("http:", "https:")):
            public_reference_url(value)
        return value

    @field_validator("manual_original_url")
    @classmethod
    def validate_original_url(cls, value: str) -> str:
        """人工指定原始来源必须是公开链接，标准 ID 可在主输入栏提供。"""
        return public_reference_url(value)


class ArticleCase(ArticleCaseInput):
    """持久化人工样本；人工意见不覆盖模型历史或来源原件。"""

    id: str
    created_at: str
    updated_at: str


class InvestigationStart(BaseModel):
    """启动一次有界调查；Agent 模式才可能发出付费模型请求。"""

    mode: Literal["rules", "agent"] = "rules"
    max_pages: int = Field(default=5, ge=1, le=12)
    policy_id: str | None = None
    request_id: str = Field(default="", max_length=100, pattern=r"^[a-zA-Z0-9_-]*$")
    max_evaluations: int = Field(default=3, ge=1, le=6)


class InvestigationRequest(BaseModel):
    """运行时不可变快照，用于复测和跨进程工具范围校验。"""

    case: ArticleCase
    options: InvestigationStart
    policy: PolicyVersion
    model_name: str = ""
    prompt_hash: str = ""
    prompt: str = ""
    intelligence_policy: IntelligencePolicyVersion | None = None
    model_base_url: str = "https://api.deepseek.com"
    model_timeout: float = 180
    model_max_tokens: int = 4096


class ReferenceLink(BaseModel):
    """实际页面中提取的外链；链接本身不证明原始来源身份。"""

    url: str
    label: str = ""
    kind: str = "link"


class PageEvidence(BaseModel):
    """一次页面访问的证据，访问失败同样显式保存。"""

    id: str
    job_id: str
    requested_url: str
    url: str
    title: str = ""
    text: str = ""
    status: str
    http_status: int | None = None
    links: list[ReferenceLink] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)
    canonical_url: str = ""
    content_hash: str = ""
    retrieved_at: str
    error: str = ""
    venue_name: str = ""
    feed_entries: list[ReferenceLink] = Field(default_factory=list)


class LocalMaterialMatch(BaseModel):
    """本地精确身份匹配，明确库、数据集和匹配依据。"""

    database: str
    dataset: str
    material_id: str
    title: str
    identity: str
    matched_by: str


class OriginalCandidate(BaseModel):
    """可能原始来源及证据；未读到原文不能写成已确认原始来源。"""

    url: str
    evidence_id: str
    reason: str
    quote: str = ""
    status: str = "linked_not_fetched"
    local_matches: list[LocalMaterialMatch] = Field(default_factory=list)


class SourceSuggestion(BaseModel):
    """可供人工审阅的来源建议，不能自动开启来源。"""

    url: str
    name: str
    suitability: Literal["candidate", "needs_connector", "needs_review", "not_recommended"] = "needs_review"
    reason: str
    evidence_id: str
    in_catalog: bool = False


class StageResult(BaseModel):
    """一个诊断节点的状态，未验证和失败不等于不相关。"""

    name: str
    status: str
    detail: str


class AgentFinding(BaseModel):
    """模型提交的结果仍需程序验证引用、页面和主题标签。"""

    relevance: Literal["core", "industry_update", "related", "out_of_scope", "insufficient_information"] = (
        "related"
    )
    reason: str = Field(min_length=1, max_length=4000)
    ai_evidence: str = Field(default="", max_length=1200)
    drug_evidence: str = Field(default="", max_length=1200)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)
    original_candidates: list[OriginalCandidate] = Field(default_factory=list, max_length=15)
    source_suggestions: list[SourceSuggestion] = Field(default_factory=list, max_length=10)


class InvestigationReport(BaseModel):
    """调查结果：程序证据、粗筛、模型判断和人工意见保持可区分。"""

    stages: list[StageResult] = Field(default_factory=list)
    pages: list[PageEvidence] = Field(default_factory=list)
    local_matches: list[LocalMaterialMatch] = Field(default_factory=list)
    original_candidates: list[OriginalCandidate] = Field(default_factory=list)
    source_suggestions: list[SourceSuggestion] = Field(default_factory=list)
    screening: GateResult | None = None
    agent_finding: AgentFinding | None = None
    notes: list[str] = Field(default_factory=list)
    intelligence_results: list[IntelligenceTask] = Field(default_factory=list)


class InvestigationJob(BaseModel):
    """一次后台任务及其不可变请求快照；重试创建新尝试记录。"""

    id: str
    status: str
    request: InvestigationRequest
    result: InvestigationReport | None = None
    error: str | None = None
    created_at: str
    updated_at: str
