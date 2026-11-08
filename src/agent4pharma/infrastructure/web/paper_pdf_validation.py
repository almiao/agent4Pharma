"""PDF 主文身份、附件边界及文本哈希校验；不负责获取文件。"""

import hashlib
import re
import unicodedata
from pathlib import Path

from pydantic import JsonValue


def validate_pdf(path: Path, identifier: str, title: str) -> dict[str, JsonValue]:
    """校验 PDF 结构及首页身份；只保存首页核验文本，不预取整篇正文。"""
    import pymupdf

    try:
        opened = pymupdf.open(path)
    except (pymupdf.FileDataError, pymupdf.EmptyFileError):
        return {"status": "invalid_pdf", "reason": "not_a_readable_pdf"}
    with opened as document:
        if not document.is_pdf or document.page_count < 1:
            return {"status": "invalid_pdf", "reason": "not_a_readable_pdf"}
        text = document[0].get_text()
        first_page_text = unicodedata.normalize("NFKC", text).casefold()
        pages = document.page_count
    matched, ancillary = _main_identity(first_page_text, identifier, title)
    text_path = path.with_suffix(".txt")
    text_path.write_text(text, encoding="utf-8")
    return {
        "status": "ancillary_document" if ancillary else "downloaded" if matched else "identity_unverified",
        "reason": "publisher_attachment_not_primary_article" if ancillary else "",
        "file": str(path.resolve()),
        "text_file": str(text_path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "bytes": path.stat().st_size,
        "pages": pages,
        "text_characters": len(text),
        "text_scope": "identity_first_page",
        "identity_match": matched,
    }


def _main_identity(first_page_text: str, identifier: str, title: str) -> tuple[bool, bool]:
    normalized = re.sub(r"\W+", "", first_page_text)
    title = unicodedata.normalize("NFKC", title).casefold()
    title_key = re.sub(r"\W+", "", title)
    doi_key = re.sub(r"\W+", "", identifier.lower())
    words = {word for word in re.findall(r"\w+", title) if len(word) > 3}
    title_overlap = len(words.intersection(set(re.findall(r"\w+", first_page_text)))) / max(len(words), 1)
    matched = (len(title_key) > 20 and title_key in normalized) or (
        len(doi_key) > 10 and doi_key in normalized and (not words or title_overlap >= 0.7)
    )
    first_page = normalized[:500]
    ancillary = ("natureportfolio" in first_page and "reportingsummary" in first_page) or any(
        first_page.startswith(marker) for marker in ("supplementaryinformation", "supportinginformation")
    )
    return matched, ancillary


def matches_main_pdf(path: Path, identifier: str, title: str) -> bool:
    """只读第一页判断是否匹配主文；目录扫描不生成旁路文本或修改下载文件。"""
    import pymupdf

    try:
        with pymupdf.open(path) as document:
            if not document.is_pdf or document.is_encrypted or document.page_count < 1:
                return False
            text = unicodedata.normalize("NFKC", document[0].get_text()).casefold()
    except (pymupdf.FileDataError, pymupdf.EmptyFileError, OSError):
        return False
    matched, ancillary = _main_identity(text, identifier, title)
    return matched and not ancillary
