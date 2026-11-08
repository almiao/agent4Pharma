"""可选PDF题头素材；独立于研究配图，保存跳过原因和准确原文身份。"""

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from agent4pharma.modules.materials.document_reading import DocumentReference


class PaperHeader(BaseModel):
    """首页连续矩形截图；缺失或失败不冒充成功，也不阻断正文制作。"""

    status: Literal["ready", "skipped", "failed"]
    reason: str = ""
    evidence_id: str = ""
    document_sha256: str = ""
    source_url: str = ""
    page: int = 1
    crop: list[float] = Field(default_factory=list)
    width: int = 0
    height: int = 0
    image_sha256: str = ""
    png_base64: str = ""
    version: str = "pdf-header-v1"


PaperHeaderReader = Callable[[DocumentReference, str], PaperHeader]
