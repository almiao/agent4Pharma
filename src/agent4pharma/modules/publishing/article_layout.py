"""科研新闻图文编排契约：固定正文、绑定原图、双渠道渲染及素材追溯。"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SerializerFunctionWrapHandler, model_serializer

from agent4pharma.modules.materials.document_reading import DocumentReference
from agent4pharma.modules.publishing.paper_header import PaperHeader


class FigureSelection(BaseModel):
    """选择已绑定论文的原图块；段落序号从1开始，不接受任意文件路径或图片URL。"""

    model_config = ConfigDict(extra="forbid")
    evidence_id: str = Field(min_length=1, max_length=200)
    page: int = Field(ge=1)
    block: int = Field(ge=1)
    after_paragraph: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=100)
    caption: str = Field(min_length=1, max_length=500)
    visual_observation: str = Field(default="", max_length=700)


class TextEmphasis(BaseModel):
    """定稿一个非空行内的准确连续文字；重复文字须选择更完整的唯一短句。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    paragraph: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=500)


class ArticleEmphasis(BaseModel):
    """强调方案绑定准确正文指纹，不改字词、顺序或审核许可。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    source_body_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: list[TextEmphasis] = Field(min_length=1, max_length=20)


class LayoutRequest(BaseModel):
    """给现有有效渠道包制作新的图文版本，保留原包与定稿身份。"""

    model_config = ConfigDict(extra="forbid")
    package_id: str
    figures: list[FigureSelection] = Field(default_factory=list, max_length=4)
    emphasis: ArticleEmphasis | None = None


class FigureRecord(BaseModel):
    """图像由宿主从论文缓存取得；记录真实源身份和图片指纹，不推断复用授权。"""

    selection: FigureSelection
    document_sha256: str
    locator: str
    image_sha256: str
    width: int
    height: int
    source_title: str
    source_url: str
    rights_status: Literal["not_verified"] = "not_verified"


@dataclass(frozen=True)
class FigureImage:
    """适配器返回的真实PNG字节和尺寸，仅在制作过程内传递。"""

    png: bytes
    width: int
    height: int
    locator: str


FigureReader = Callable[[DocumentReference, FigureSelection], FigureImage]


class ArticlePresentation(BaseModel):
    """与正文一起冻结的公众号片段、原图记录及确定性制作耗时。"""

    template_version: str = "science-reading-v2"
    wechat_html: str
    wechat_hash: str
    figures: list[FigureRecord] = Field(default_factory=list)
    elapsed_seconds: float = 0
    model_calls: int | None = 0
    total_tokens: int | None = 0
    selection_task_id: str = ""
    source_body_hash: str
    checks: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    emphasis: ArticleEmphasis | None = None
    paper_header: PaperHeader | None = None
    excluded_source_ids: list[str] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def serialize_presentation(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        """旧图文包省略空强调、题头和来源显示设置，保持历史批准指纹。"""
        result = handler(self)
        if result["emphasis"] is None:
            result.pop("emphasis")
        if self.paper_header is None:
            result.pop("paper_header", None)
        if not self.excluded_source_ids:
            result.pop("excluded_source_ids", None)
        return result
