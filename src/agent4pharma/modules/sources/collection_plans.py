"""配置驱动网页采集的版本化公开契约；规则是数据，不允许执行代码。"""

import hashlib
from typing import Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from agent4pharma.shared.url_validation import public_reference_url


class PlanModel(BaseModel):
    """拒绝未知配置键，防止拼写错误静默失效。"""

    model_config = ConfigDict(extra="forbid")


class ExtractField(PlanModel):
    """按顺序尝试 CSS 选择器，读取首个非空文本或指定属性。"""

    selectors: list[str] = Field(default_factory=list, max_length=12)
    attribute: str = ""


class DiscoveryEntry(PlanModel):
    """一个有限的 RSS 或 HTML 发现窗口；空结果需显式允许。"""

    url: str = Field(min_length=1, max_length=2000)
    kind: Literal["rss", "html"]
    link_selector: str = ""
    next_selector: str = ""
    max_pages: int = Field(default=1, ge=1, le=10)
    max_links: int = Field(default=30, ge=1, le=200)
    allow_empty: bool = False
    order: Literal["source", "newest_first"] = "source"

    @model_serializer(mode="wrap")
    def serialize_entry(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        """默认来源顺序沿用旧 JSON 身份；显式新排序才进入版本指纹。"""
        result = handler(self)
        if self.order == "source":
            result.pop("order", None)
        return result

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """入口只能是公开永久 URL，不携带凭证。"""
        return public_reference_url(value)

    @model_validator(mode="after")
    def require_links(self) -> "DiscoveryEntry":
        """HTML 列表必须显式指定链接规则。"""
        if self.kind == "html" and not self.link_selector:
            raise ValueError("HTML 发现入口必须配置 link_selector")
        if self.kind != "rss" and self.order != "source":
            raise ValueError("按日期排序目前只支持带逐篇日期的 RSS/Atom 入口")
        return self


class ArticleRules(PlanModel):
    """详情字段、正文边界和付费预览识别规则，不包含站点代码。"""

    title: ExtractField = Field(default_factory=lambda: ExtractField(selectors=["h1"]))
    body: ExtractField
    canonical: ExtractField = Field(
        default_factory=lambda: ExtractField(selectors=['link[rel="canonical"]'], attribute="href")
    )
    external_id: ExtractField = Field(default_factory=ExtractField)
    author: ExtractField = Field(default_factory=ExtractField)
    published: ExtractField = Field(
        default_factory=lambda: ExtractField(
            selectors=['meta[property="article:published_time"]'], attribute="content"
        )
    )
    modified: ExtractField = Field(
        default_factory=lambda: ExtractField(
            selectors=['meta[property="article:modified_time"]'], attribute="content"
        )
    )
    remove_selectors: list[str] = Field(default_factory=lambda: ["script", "style", "nav", "footer"])
    paywall_selectors: list[str] = Field(default_factory=list)
    paywall_text: list[str] = Field(default_factory=list)
    minimum_body_chars: int = Field(default=100, ge=1, le=10000)
    maximum_body_chars: int = Field(default=60000, ge=100, le=200000)


class RssFieldMeasurement(PlanModel):
    """逐源真实字段观察，书目信息不计作摘要。"""

    field: str
    present: int
    total: int
    median_chars: float
    role: Literal["title", "semantic_text", "bibliographic_only", "category", "identity", "provenance"]


class RssExtractionProfile(PlanModel):
    """来源拥有的 RSS 提取规则和核验依据；新规则进入采集快照。"""

    version: str = "rss-signal-v2"
    journal_name: str = ""
    text_fields: list[str] = Field(default_factory=list)
    category_fields: list[str] = Field(default_factory=lambda: ["category", "subject"])
    measurements: list[RssFieldMeasurement] = Field(default_factory=list)
    inspected_at: str
    evidence_sha256: str
    note: str = "简介/导读不自动视为完整摘要；日期精度不足保留原值。"


class CollectionPlan(PlanModel):
    """一来源多发现入口共享身份与详情规则；每日触发使用既有调度。"""

    source_id: str = Field(min_length=1, max_length=150)
    name: str = Field(min_length=1, max_length=200)
    homepage_url: str = Field(min_length=1, max_length=2000)
    enabled: bool = True
    allowed_hosts: list[str] = Field(min_length=1, max_length=20)
    entries: list[DiscoveryEntry] = Field(min_length=1, max_length=10)
    article: ArticleRules
    material_type: Literal["news_report", "editorial_article", "newsletter", "blog_post"] = "news_report"
    coarse_mode: Literal["keywords", "semantic_only"] = "semantic_only"
    acquisition_mode: Literal["article_details", "feed_metadata"] = "article_details"
    feed_date_policy: Literal["publication_window", "current_feed"] = "publication_window"
    source_kind: Literal["news_site", "journal_site"] = "news_site"
    rss_profile: RssExtractionProfile | None = None

    @model_serializer(mode="wrap")
    def serialize_plan(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        """旧详情计划保留原指纹；明确的 RSS 元数据模式才进入新版本身份。"""
        result = handler(self)
        if self.acquisition_mode == "article_details":
            result.pop("acquisition_mode", None)
        if self.source_kind == "news_site":
            result.pop("source_kind", None)
        if self.rss_profile is None:
            result.pop("rss_profile", None)
        if self.feed_date_policy == "publication_window":
            result.pop("feed_date_policy", None)
        return result

    @model_validator(mode="after")
    def validate_feed_metadata(self) -> "CollectionPlan":
        """元数据模式读取完整的单个当前 Feed，不套用详情队列截断或排序。"""
        if self.acquisition_mode == "feed_metadata" and (
            len(self.entries) != 1 or self.entries[0].kind != "rss" or self.entries[0].order != "source"
        ):
            raise ValueError("feed_metadata 仅支持一个 RSS 入口及来源顺序")
        if self.feed_date_policy != "publication_window" and self.acquisition_mode != "feed_metadata":
            raise ValueError("feed_date_policy=current_feed 仅支持 feed_metadata 模式")
        return self

    @field_validator("homepage_url")
    @classmethod
    def validate_homepage(cls, value: str) -> str:
        """主页不接受凭证或非 Web 协议。"""
        return public_reference_url(value)

    def fingerprint(self) -> str:
        """返回完整提取配置的稳定版本，历史运行冻结此定义。"""
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class CollectionPlanCatalog(Protocol):
    """来源模块拥有配置及启停状态，调度仅通过此公开契约读取。"""

    def get(self, source_id: str) -> CollectionPlan:
        """读取数据库中的单个计划；不存在时抛出 KeyError。"""
        ...

    def save(self, plan: CollectionPlan, *, expected_hash: str | None = None) -> CollectionPlan:
        """原子保存目录、版本和入口；更新需匹配预期 hash，拒绝覆盖并发修改。"""
        ...

    def list_plans(self, *, enabled_only: bool = False) -> list[CollectionPlan]:
        """读取经校验的当前计划，可仅返回已关注且启用的配置。"""
        ...
