"""绑定已入库原文的分页阅读契约，不授予模型任意路径或远程上传权限。"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field


class DocumentContinuation(BaseModel):
    """解析器返回的原始续读定位，页码与段落定位不重写。"""

    page_range: str | None = None
    after: str | None = None
    locator: str | None = None


class DocumentReference(BaseModel):
    """只含已关联主文身份与页数，不携带论文正文或本地文件路径。"""

    id: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pages: int = Field(ge=1)
    parser: str = "mineru-standard-v1"


class DocumentPreparation(BaseModel):
    """全篇解析准备状态，ready 不表示模型已经读过全文。"""

    sha256: str
    status: Literal["ready", "pending", "unavailable", "failed"]
    error_code: str = ""


class DocumentEvidenceRead(BaseModel):
    """一次按需读取的有限片段，供模型引用而不是把整篇论文加入上下文。"""

    status: Literal["available", "pending", "unavailable", "failed"]
    evidence_id: str = ""
    content_hash: str = ""
    text: str = ""
    locators: list[str] = Field(default_factory=list)
    truncated: bool = False
    next_request: DocumentContinuation | None = None
    already_read: bool = False
    reads_remaining: int | None = None
    next_action: str = ""
    error_code: str = ""


class DocumentContentRange(BaseModel):
    """解析器返回的原始起止定位，不从排版文本猜测页码。"""

    page_range: str | None = None
    start: str
    end: str


class DocumentContent(BaseModel):
    """经文件哈希绑定的正文和可追溯范围；格式由解析器声明。"""

    sha256: str
    short_id: str
    tier: str
    content: str
    content_ranges: list[DocumentContentRange] = Field(default_factory=list)
    truncated: bool = False
    next_request: DocumentContinuation | None = None


class DocumentReadResult(BaseModel):
    """解析成功、仍在排队与不可用必须明确区分。"""

    status: Literal["available", "pending", "unavailable", "failed"]
    content: DocumentContent | None = None
    error_code: str = ""
    retryable: bool = False
    locators: list[str] = Field(default_factory=list)


class DocumentParser(Protocol):
    """宿主选择文件和页窗；实现不得静默上传或更换质量等级。"""

    def parse(
        self, path: Path, sha256: str, start_page: int, end_page: int, after: str = ""
    ) -> DocumentReadResult:
        """限时解析已验证文件，返回正文、定位或技术状态。"""
        ...


@dataclass(frozen=True)
class VerifiedDocument:
    """由资料账本验证后交给解析器的宿主文件，不由模型提交。"""

    path: Path
    sha256: str
    pages: int


@dataclass
class BoundDocumentReader:
    """每次读前重新验证原文，拒绝范围越界与文件被替换。"""

    document: Callable[[], VerifiedDocument]
    parser: DocumentParser

    def read_document(self, start_page: int = 1, end_page: int = 1, after: str = "") -> DocumentReadResult:
        """读取绑定原文的至多三页；页窗内仍可能截断，须遵循返回的续读信息。"""
        document = self.document()
        if not 1 <= start_page <= end_page or end_page - start_page >= 3:
            raise ValueError("每次解析需指定连续1至3页")
        if start_page > document.pages:
            raise ValueError("起始页超出原文页数")
        if len(after) > 2048 or any(ord(character) < 32 for character in after):
            raise ValueError("续读游标无效")
        if after:
            result = self.parser.parse(
                document.path, document.sha256, start_page, min(end_page, document.pages), after
            )
        else:
            result = self.parser.parse(
                document.path, document.sha256, start_page, min(end_page, document.pages)
            )
        if self.document() != document:
            raise ValueError("读取期间文档身份已变化")
        return result
