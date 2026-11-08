"""本地PDF首页保守裁切；不调用模型、OCR、网络或重排原文。"""

import base64
import hashlib
import re
import unicodedata
from dataclasses import dataclass

from agent4pharma.modules.materials.document_reading import VerifiedDocument
from agent4pharma.modules.publishing.paper_header import PaperHeader


@dataclass(frozen=True)
class HeaderLine:
    """PDF中的真实文字行和边界，以point为单位。"""

    text: str
    box: tuple[float, float, float, float]
    size: float


def _key(text: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text).casefold())


def _author_rows(lines: list[HeaderLine]) -> list[HeaderLine]:
    """合并被ORCID图标拆开的同一作者行；保留远离作者的侧栏边界。"""
    rows: list[list[HeaderLine]] = []
    for line in sorted(lines, key=lambda item: (item.box[3], item.box[0])):
        row = next((row for row in rows if abs(row[0].box[3] - line.box[3]) <= 2), None)
        if row is None:
            rows.append([line])
        else:
            row.append(line)
    merged: list[HeaderLine] = []
    for row in rows:
        current = None
        for line in sorted(row, key=lambda item: item.box[0]):
            if current and 0 <= line.box[0] - current.box[2] <= 16 and abs(line.size - current.size) <= 1:
                current = HeaderLine(
                    current.text + " " + line.text,
                    (
                        current.box[0],
                        min(current.box[1], line.box[1]),
                        line.box[2],
                        max(current.box[3], line.box[3]),
                    ),
                    max(current.size, line.size),
                )
                merged[-1] = current
            else:
                current = line
                merged.append(line)
    return merged


def _crop(lines: list[HeaderLine], title: str, width: float, height: float) -> tuple[float, ...] | None:
    key = _key(title)
    title_lines = [
        line
        for line in lines
        if line.size >= 13
        and len(_key(line.text)) >= 8
        and _key(line.text) in key
        and line.box[1] < height * 0.45
    ]
    if len(key) < 20 or _key("".join(line.text for line in title_lines)) != key:
        return None
    bottom = max(line.box[3] for line in title_lines)
    left = min(line.box[0] for line in title_lines)
    authors = [
        line
        for line in lines
        if bottom - 2 <= line.box[1] <= bottom + 110 and line.box[0] >= left - 15 and 6 <= line.size <= 13
    ]
    # 只接受紧邻题名的短作者行；遇到段落、摘要或机构地址就结束。
    chosen = []
    for line in _author_rows(authors):
        if re.search(r"received|accepted|published|https?", line.text, re.I):
            continue
        if chosen and line.box[0] < chosen[0].box[0] - 15:
            continue
        if re.search(r"abstract|university|institute|department", line.text, re.I):
            break
        names = re.findall(r"\b[A-ZÀ-Ž][a-zà-ž]+[ \u00a0]+[A-ZÀ-Ž][a-zà-ž]+", line.text)
        if not names or len(line.text.split()) > 38 or line.text.rstrip().endswith("."):
            break
        chosen.append(line)
    if not chosen or len(chosen) > 4:
        return None
    bottom = max(line.box[3] for line in chosen) + 5
    top = min(line.box[1] for line in lines if line.box[1] < bottom) - 5
    included = [line for line in lines if line.box[1] < bottom and line.box[3] > top]
    # 不裁掉同高度的侧栏文字，也不为了纳入页脚刊名而拉长截图。
    if any(line.box[3] > bottom + 1 for line in included):
        bottom = max(line.box[3] for line in included) + 3
        if any(line.box[1] < bottom < line.box[3] for line in lines):
            return None
    x0 = max(0, min(line.box[0] for line in included) - 5)
    x1 = min(width, max(line.box[2] for line in included) + 5)
    top = max(0, top)
    if bottom > height * 0.48 or bottom - top > (x1 - x0) * 0.5:
        return None
    return x0, top, x1, bottom


def capture_pdf_header(document: VerifiedDocument, title: str) -> PaperHeader:
    """只读绑定PDF；已知读取错误显式返回failed，身份不符不取图，布局不确定则skipped。"""
    import pymupdf

    identity = {"document_sha256": document.sha256, "version": "pdf-header-v2"}
    try:
        content = document.path.read_bytes()
        if hashlib.sha256(content).hexdigest() != document.sha256:
            return PaperHeader(status="failed", reason="题头PDF身份与绑定资料不符", **identity)
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            if pdf.is_encrypted or not pdf.page_count:
                return PaperHeader(status="skipped", reason="首页不可读取", **identity)
            page = pdf[0]
            if page.rotation:
                return PaperHeader(status="skipped", reason="首页旋转，省略自动题头", **identity)
            lines = []
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    spans = line["spans"]
                    text = "".join(span["text"] for span in spans).strip()
                    if text:
                        lines.append(HeaderLine(text, tuple(line["bbox"]), max(s["size"] for s in spans)))
            lines.sort(key=lambda line: (round(line.box[1], 1), line.box[0]))
            crop = _crop(lines, title, page.rect.width, page.rect.height)
            if crop is None:
                return PaperHeader(status="skipped", reason="题名或作者区不确定，或题头过高", **identity)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=pymupdf.Rect(crop), alpha=False)
            png = pix.tobytes("png")
            if len(png) > 3_000_000:
                return PaperHeader(status="skipped", reason="题头图片过大", **identity)
            return PaperHeader(
                status="ready",
                crop=list(crop),
                width=pix.width,
                height=pix.height,
                image_sha256=hashlib.sha256(png).hexdigest(),
                png_base64=base64.b64encode(png).decode("ascii"),
                **identity,
            )
    except (OSError, pymupdf.FileDataError, pymupdf.EmptyFileError):
        return PaperHeader(status="failed", reason="题头PDF读取失败", **identity)
