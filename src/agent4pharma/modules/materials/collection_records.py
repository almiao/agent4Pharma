"""连接器与持久化之间的资料契约；来源时间精度保持原样，不推定缺失时区。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypedDict

from pydantic import BaseModel, JsonValue


class RssTextSignal(TypedDict):
    """从实际 RSS 字段清理出的语义文本。"""

    field: str
    text: str
    truncated: bool


class AuthorAffiliationEvidence(BaseModel):
    """条目内实际作者单位及字段出处；不等同于已核实的机构排名或逐人对应。"""

    field: str
    text: str
    origin: Literal["rss_xml", "rss_html"]


class RssSignal(TypedDict):
    """归档资料携带的结构化 RSS 判别输入，不表示完整摘要。"""

    journal: str
    profile_version: str
    fields: list[RssTextSignal]
    categories: list[str]
    article_types: list[str]
    information_status: str


class SourceRecordSnapshot(BaseModel):
    """资料模块对外提供的已归档来源版本，不暴露数据库连接。"""

    source_record_id: str
    source_id: str
    title: str
    abstract: str
    url: str
    metadata: dict[str, JsonValue]
    identifiers: dict[str, str] = {}


class SourceRecordReading(BaseModel):
    """按来源版本读取的已归档文章；正文分页，不将摘要或片段冒充全文。"""

    source_record_id: str
    title: str
    url: str
    published: str | None = None
    abstract: str = ""
    body: str = ""
    availability: Literal["fulltext", "partial", "abstract_only", "link_only", "restricted", "unavailable"]
    title_is_provisional: bool = False
    total_chars: int = 0
    next_offset: int | None = None
    read_error: str = ""


@dataclass(frozen=True)
class CollectedLink:
    """正文中实际出现的公开引用链接，不推定为原始研究。"""

    url: str
    label: str


@dataclass(frozen=True)
class CollectedContent:
    """实际读取的文章文本及访问范围；preview/truncated 不能声明为全文。"""

    text: str
    access_state: Literal["fulltext", "preview", "truncated"]
    parser_version: str
    links: list[CollectedLink] = field(default_factory=list)


@dataclass
class CollectedRecord:
    """连接器取得的资料元数据；外部 ID 和版本仍保留来源语义。"""

    source: str
    external_id: str
    title: str
    abstract: str = ""
    authors: list[str] = field(default_factory=list)
    identifiers: dict[str, str] = field(default_factory=dict)
    version: str = ""
    url: str = ""
    published: str | None = None
    updated: str | None = None
    metadata: dict = field(default_factory=dict)
    content: CollectedContent | None = None


@dataclass
class SourceResponse:
    """原始成功响应，URL 必须已经移除密钥参数。"""

    body: bytes
    url: str  # Request URL with secret query parameters removed.
    content_type: str


@dataclass
class CollectionPage:
    """一个来源页与其原始响应、下一游标及覆盖说明；下一游标为空表示结束。"""

    records: list[CollectedRecord]
    next_cursor: dict | None
    raw: list[SourceResponse]
    total: int
    date_basis: str
    deferred_error: str | None = None


@dataclass(frozen=True)
class PersistedRecord:
    """Identity and prior existence observed inside a page transaction."""

    material_id: str
    record_id: str
    existed: bool
    known: bool
