"""公众号草稿交付契约；账号、准确包与请求共同确定一次外部副作用。"""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WechatDraftRequest(BaseModel):
    """只接受已批准包，封面来自包内图片或目标账号的永久素材。"""

    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    package_id: str = Field(min_length=1)
    expected_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    account_id: str = Field(min_length=1)
    cover_sha256: str = Field(default="", pattern=r"^([0-9a-f]{64})?$")
    thumb_media_id: str = Field(default="", max_length=256, pattern=r"^[\w-]*$")

    @model_validator(mode="after")
    def require_cover(self) -> "WechatDraftRequest":
        """两种封面来源必须且只能选择一种，不能自动猜选。"""
        if bool(self.cover_sha256) == bool(self.thumb_media_id):
            raise ValueError("请选择一张包内封面图片，或填写永久封面素材 ID")
        return self


class WechatDeliveryRecord(BaseModel):
    """持久化单次交付；提交结果未知时禁止新请求绕过防重。"""

    id: str
    request: WechatDraftRequest
    status: Literal["queued", "uploading", "submitting", "submitted", "failed", "unknown_delivery"]
    created_at: str
    updated_at: str
    remote_id: str = ""
    error: str = ""
    resolution: str = ""


class WechatConfirmAbsent(BaseModel):
    """人工实际核对草稿箱后确认不存在；只解除阻断，不立即重发。"""

    model_config = ConfigDict(extra="forbid")
    delivery_id: UUID
    account_id: str = Field(min_length=1)
    confirmed_absent: Literal[True]
    reason: str = Field(min_length=5, max_length=500)


class WechatImageOption(BaseModel):
    """准确图文中可选的封面图片身份与展示名称。"""

    sha256: str
    label: str


class WechatDeliveryState(BaseModel):
    """无网络的交付读模型；不公开账号密钥或 token。"""

    configured: bool
    account_id: str
    account_name: str
    fingerprint: str
    available: bool
    reason: str = ""
    images: list[WechatImageOption] = Field(default_factory=list)
    deliveries: list[WechatDeliveryRecord] = Field(default_factory=list)


@dataclass(frozen=True)
class WechatImage:
    """从批准 HTML 提取并与图片指纹核对过的 PNG。"""

    sha256: str
    png: bytes


@dataclass(frozen=True)
class WechatDraftContent:
    """准备传输的准确标题、HTML 与包内图像，不接受外部文件路径。"""

    title: str
    html: str
    images: tuple[WechatImage, ...]


@dataclass(frozen=True)
class WechatPreparedDraft:
    """素材上传完成后的正文和永久封面 ID；仅在本次交付内使用。"""

    title: str
    html: str
    thumb_media_id: str


class WechatDeliveryError(Exception):
    """可公开的固定错误，不携带供应商原始响应、请求 URL 或凭证。"""


class WechatDeliveryUnknown(WechatDeliveryError):
    """创建草稿请求可能已被接收，不能自动重发。"""


class WechatDraftGateway(Protocol):
    """网络适配端口；素材准备与有歧义风险的草稿创建明确分开。"""

    def prepare(self, content: WechatDraftContent, request: WechatDraftRequest) -> WechatPreparedDraft:
        """上传素材；失败尚未创建草稿，可由用户重新请求。"""
        ...

    def create(self, prepared: WechatPreparedDraft) -> str:
        """只发送一次建草稿请求，返回 media_id，未知结果抛专用异常。"""
        ...
