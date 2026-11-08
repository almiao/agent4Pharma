"""单篇回归的版本化请求与真实阶段结果；复用正式业务用例而非造候选。"""

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent4pharma.modules.sources.collection_plans import ArticleRules
from agent4pharma.runtime.production_execution import ProductionLimits
from agent4pharma.shared.url_validation import public_reference_url


class ArticleRegressionRequest(BaseModel):
    """受限的单篇真实源/模型验证请求；不自动关注整站或执行对外发布。"""

    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2000)
    fallback_urls: list[str] = Field(default_factory=list, max_length=3)
    rules: ArticleRules
    expected_title: str = Field(default="", max_length=1000)
    allow_model: bool = False
    max_production_tasks: int = Field(default=6, ge=1, le=10)
    production_limits: ProductionLimits = Field(
        default_factory=lambda: ProductionLimits(max_tokens=8192, timeout_seconds=300, max_reads=12)
    )
    request_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    discovery_source_id: str = Field(default="", max_length=150, pattern=r"^[a-zA-Z0-9_-]*$")

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """主来源不允许凭证或非公开地址。"""
        return public_reference_url(value)

    @field_validator("fallback_urls")
    @classmethod
    def validate_fallbacks(cls, values: list[str]) -> list[str]:
        """备用来源逐项使用相同访问边界。"""
        return [public_reference_url(value) for value in values]


class ArticleRegressionRun(BaseModel):
    """一次单篇功能回归的关联账本，不把未执行阶段记成成功。"""

    id: str
    request: ArticleRegressionRequest
    status: Literal["queued", "running", "blocked", "failed", "complete"] = "queued"
    subject_refs: list[str] = Field(default_factory=list)
    material_ref: str = ""
    assessment_ref: str = ""
    candidate_ref: str = ""
    story_ref: str = ""
    package_ref: str = ""
    error: str = ""
    created_at: str
    updated_at: str


class ArticleRegressionRepository(Protocol):
    """回归账本存储端口；正文原件由资料模块保存。"""

    def get(self, identity: str) -> ArticleRegressionRun:
        """读取运行。"""
        ...

    def list(self) -> list[ArticleRegressionRun]:
        """读取有限历史用于管理界面。"""
        ...
