"""来源管理输入与公开契约；来源目录启用不等于已接入每日采集。"""

from typing import Literal, Protocol

from pydantic import AwareDatetime, BaseModel, Field, field_validator

from agent4pharma.shared.url_validation import public_reference_url


class SourceAccessCheck(BaseModel):
    """单次公开入口复核，不保存凭证或响应正文；哈希和结果可追溯。"""

    url: str
    checked_at: AwareDatetime
    mode: Literal["configured_entry", "feed_accept", "homepage", "discovered_feed"]
    outcome: Literal[
        "feed", "empty_feed", "http_error", "not_feed", "parse_error", "request_error", "identity_mismatch"
    ]
    http_status: int | None = Field(default=None, ge=100, le=599)
    final_url: str = ""
    reason: str = Field(default="", max_length=1500)
    entries: int = Field(default=0, ge=0)
    sha256: str = ""

    @field_validator("url", "final_url")
    @classmethod
    def validate_public_url(cls, value: str) -> str:
        """拒绝将内嵌凭证或签名 URL 存入可见复核记录。"""
        return public_reference_url(value)


class SourceCollectionAvailability(BaseModel):
    """来源当前采集可用性，独立于关注/暂停和某次任务状态，不宣称永久不可用。"""

    status: Literal["available", "currently_unavailable", "needs_configuration"]
    reason: str = Field(min_length=1, max_length=2000)
    checked_at: AwareDatetime
    checks: list[SourceAccessCheck] = Field(min_length=1, max_length=12)


class SourceEdit(BaseModel):
    """可人工编辑的来源及入口，不覆盖抓取原件和既有身份。"""

    name: str = Field(min_length=1, max_length=200)
    source_kind: str = Field(default="news_site", max_length=60)
    homepage_url: str = Field(default="", max_length=2000)
    endpoint_url: str = Field(default="", max_length=2000)
    access_method: str = Field(default="html", pattern="^(api|rss|html|browser|app)$")
    connector_key: str = Field(default="", max_length=100)
    status: str = Field(default="candidate", pattern="^(candidate|watch|paused)$")
    notes: str = Field(default="", max_length=4000)
    scope_verified: bool = False
    revision: int | None = None


class ManagedSource(SourceEdit):
    """来源记录和真实连接能力投影，保留目录与调度边界。"""

    id: str
    endpoint_id: str | None = None
    organization: str | None = None
    material_count: int = 0
    dataset: str = "main"
    capability: str = "登记不等于接入；已确认的有效 RSS 可采集，其他入口需实际连接器"
    collection_availability: SourceCollectionAvailability | None = None


class SourceDirectoryFilters(BaseModel):
    """目录组合筛选；未复核独立于不可用，分页偏移应用于筛选后的集合。"""

    availability: Literal["", "available", "currently_unavailable", "needs_configuration", "unverified"] = ""
    kind: str = Field(default="", max_length=60)
    status: Literal["", "candidate", "watch", "paused"] = ""
    offset: int = Field(default=0, ge=0)


class SourceCatalog(Protocol):
    """管理界面需要的来源存取能力，不暴露数据库连接。"""

    def list_sources(
        self, search: str = "", limit: int = 100, *, filters: SourceDirectoryFilters | None = None
    ) -> list[ManagedSource]:
        """先组合筛选再分页查询来源，不把关注状态当作采集可用性。"""
        ...

    def source_kinds(self) -> list[str]:
        """返回完整目录实际登记的类型，不受当前搜索和分页截断影响。"""
        ...

    def save_source(self, change: SourceEdit, source_id: str | None = None) -> ManagedSource:
        """保存来源与入口，版本冲突时失败，不自动启用入口。"""
        ...

    def record_collection_availability(
        self, source_id: str, assessment: SourceCollectionAvailability, expected_revision: int
    ) -> None:
        """保存有证据的当前可用性；版本冲突和过时复核失败，不改历史任务或启停。"""
        ...


class DirectoryEndpoint(BaseModel):
    """目录中的真实访问入口，不返回接入凭证。"""

    id: str
    url: str
    access_method: str
    connector_key: str | None = None
    enabled: bool


class DirectorySource(BaseModel):
    """来源登记与正式关注状态。"""

    id: str
    name: str | None = None
    status: str
    revision: int
    dataset: str
    followed: bool
    endpoints: list[DirectoryEndpoint]


class DirectoryVenue(BaseModel):
    """载体的身份与关注状态，不以名称存在代表已接入。"""

    id: str
    name: str
    kind: str
    watch_status: str
    homepage_url: str | None = None


class DirectoryInspection(BaseModel):
    """按同域来源和同名载体分别核对的结果。"""

    reference: str
    sources: list[DirectorySource]
    venues: list[DirectoryVenue]
