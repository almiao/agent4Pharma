"""本地文字渠道包与外部发行端口，远端未知状态不能作为失败重发。"""

import hashlib
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SerializerFunctionWrapHandler, model_serializer

from agent4pharma.modules.publishing.article_layout import (
    ArticleEmphasis,
    ArticlePresentation,
    FigureSelection,
)
from agent4pharma.modules.publishing.article_renderer import render_article


class ChannelSubmission(BaseModel):
    """发行角色决定排版和渠道意向；首版不允许实质改写审核合格正文。"""

    model_config = ConfigDict(extra="forbid")
    channel: Literal["local_html", "local_markdown"]
    release_note: str = Field(min_length=10, max_length=2000)
    figures: list[FigureSelection] = Field(default_factory=list, max_length=4)
    emphasis: ArticleEmphasis | None = None


class ChannelPackage(BaseModel):
    """可预览的准确文字产物，内容 hash 与审核引用共同构成发行身份。"""

    id: str
    story_id: str
    story_revision: int
    draft_id: str
    review_id: str
    channel: Literal["local_html", "local_markdown"]
    body: str
    content_hash: str
    release_note: str
    created_at: str
    presentation: ArticlePresentation | None = None
    edit_root_id: str = ""
    edit_parent_id: str = ""
    edit_pending: bool = False

    @model_serializer(mode="wrap")
    def serialize_package(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        """旧包省略编辑元数据，保持已有人工批准指纹。"""
        result = handler(self)
        for key in ("edit_root_id", "edit_parent_id", "edit_pending"):
            if not getattr(self, key):
                result.pop(key, None)
        return result


class ReleaseReceipt(BaseModel):
    """一次发行的真实回执；本地导出与对外发布状态明确分开。"""

    id: str
    package_id: str
    status: Literal["exported", "submitted", "published", "unknown_delivery", "failed"]
    remote_id: str = ""
    url: str = ""
    created_at: str


class DeliveryAdapter(Protocol):
    """未来受授权的渠道适配端口；不能通过模型任意选择账号或目标。"""

    def deliver(self, package: ChannelPackage, idempotency_key: str) -> ReleaseReceipt:
        """在数据库事务外发送；无法确认时返回 unknown_delivery。"""
        ...


def render_package(title: str, body: str, channel: str) -> tuple[str, str]:
    """从已审核文本生成无脚本 HTML 或 Markdown；不允许发行模型改事实。"""
    if channel == "local_html":
        content, _ = render_article(title, body, [], [])
    elif channel == "local_markdown":
        content = f"# {title}\n\n{body}\n"
    else:
        raise ValueError("未接入此渠道；不能假定具备账号发送能力")
    return content, hashlib.sha256(content.encode()).hexdigest()
