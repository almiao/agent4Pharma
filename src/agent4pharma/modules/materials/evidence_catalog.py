"""模型初始上下文中的证据目录：显示身份与范围，不携带正文。"""

import hashlib
from typing import Protocol

from pydantic import BaseModel, Field

from agent4pharma.modules.materials.document_reading import DocumentReference


class CatalogSource(Protocol):
    """资料与制作侧证据共同提供的只读目录字段。"""

    id: str
    title: str
    text: str
    url: str
    origin: str
    document: DocumentReference | None
    read_scope: str
    locators: list[str]


class EvidenceCatalogEntry(BaseModel):
    """只含证据索引；模型需显式读取片段才能获得对应正文。"""

    id: str
    title: str
    url: str
    origin: str
    characters: int
    content_hash: str
    document: DocumentReference | None = None
    read_scope: str = ""
    locators: list[str] = Field(default_factory=list)


def catalog_entry(source: CatalogSource) -> EvidenceCatalogEntry:
    """计算目录身份，不复制来源的正文到返回值。"""
    return EvidenceCatalogEntry(
        id=source.id,
        title=source.title,
        url=source.url,
        origin=source.origin,
        characters=len(source.text),
        content_hash=hashlib.sha256(source.text.encode()).hexdigest(),
        document=source.document,
        read_scope=source.read_scope,
        locators=source.locators,
    )
