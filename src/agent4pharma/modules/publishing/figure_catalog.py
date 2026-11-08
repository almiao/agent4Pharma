"""论文配图检索契约；模型只持有绑定文档内的短定位，不能读取任意路径。"""

from collections.abc import Callable

from pydantic import BaseModel, Field

from agent4pharma.modules.materials.document_reading import DocumentReference
from agent4pharma.modules.publishing.article_layout import FigureImage


class FigureCandidate(BaseModel):
    """图块及邻近文本；邻近标签仅供候选定位，不能当已确认的面板身份。"""

    figure_id: str
    page: int
    block: int
    nearby_text: str


class FigurePage(BaseModel):
    """含图片的页面，caption_context来自相邻缓存页，需实际看图确认。"""

    page: int
    overview_id: str
    caption_context: str
    candidates: list[FigureCandidate]


class FigureCatalog(BaseModel):
    """宿主一次扫描解析缓存后持久复用的图片索引，不包含图片base64。"""

    version: str = "figure-index-v2"
    short_id: str = ""
    document_sha256: str
    pages: list[FigurePage]


class FigureSearchResult(BaseModel):
    """每次至多三个候选页，无全量翻页接口；重复查询不重复输出目录。"""

    evidence_id: str
    pages: list[FigurePage] = Field(default_factory=list)
    queries_remaining: int
    images_remaining: int
    already_returned: bool = False
    reused_pages: list[int] = Field(default_factory=list)
    note: str = "先按新闻主张检索；可看候选页总览，再按子图ID看图。邻近文本不保证图号对应。"


FigureCatalogReader = Callable[[DocumentReference], FigureCatalog]
FigureInspector = Callable[[DocumentReference, str], FigureImage]
