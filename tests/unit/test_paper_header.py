import base64
import hashlib

import pymupdf
import pytest

from agent4pharma.infrastructure.llm.pdf_paper_header import capture_pdf_header
from agent4pharma.modules.materials.document_reading import VerifiedDocument
from agent4pharma.modules.publishing.article_layout import ArticlePresentation
from agent4pharma.modules.publishing.article_renderer import render_article
from agent4pharma.workflows.article_product_editing import EmbeddedImages

TITLE = "A compact experimental laboratory for autonomous discovery"


def make_pdf(tmp_path, *, author_y=125, authors=True):
    path = tmp_path / "paper.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        page.insert_text((40, 30), "Example Journal", fontsize=12)
        page.insert_text((40, 65), "A compact experimental laboratory", fontsize=20)
        page.insert_text((40, 90), "for autonomous discovery", fontsize=20)
        if authors:
            page.insert_text((40, author_y), "Alice Smith, Bob Jones & Carol White", fontsize=10)
        page.insert_text((40, author_y + 45), "Abstract", fontsize=12)
        page.insert_text((40, author_y + 65), "This study describes experimental results.", fontsize=10)
        pdf.save(path)
    return VerifiedDocument(path, hashlib.sha256(path.read_bytes()).hexdigest(), 1)


def test_compact_header_contains_title_authors_without_abstract(tmp_path):
    doc = make_pdf(tmp_path)
    header = capture_pdf_header(doc, TITLE)
    assert header.status == "ready"
    assert header.height / header.width <= 0.5
    assert header.crop[3] < 150
    assert header.crop[3] > 125
    png = base64.b64decode(header.png_base64)
    assert hashlib.sha256(png).hexdigest() == header.image_sha256
    with pymupdf.open(doc.path) as pdf:
        text = pdf[0].get_text(clip=pymupdf.Rect(header.crop))
    assert "Example Journal" in text and "Alice Smith" in text
    assert "Abstract" not in text


def test_header_joins_author_fragments_without_truncating_second_row(tmp_path):
    path = tmp_path / "fragmented-authors.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        page.insert_text((40, 30), "Example Journal", fontsize=12)
        page.insert_text((40, 65), "A compact experimental laboratory", fontsize=20)
        page.insert_text((40, 90), "for autonomous discovery", fontsize=20)
        # 独立文字对象模拟被ORCID图标分隔的姓名与上标；侧栏不应并入作者行。
        page.insert_text((40, 125), "Received: 1 May 2025", fontsize=8)
        page.insert_text((220, 125), "Alice Smith", fontsize=10)
        page.insert_text((280, 125), "1, Bob Jones", fontsize=10)
        page.insert_text((343, 125), "2,", fontsize=10)
        page.insert_text((220, 140), "Carol White", fontsize=10)
        page.insert_text((282, 140), "3", fontsize=10)
        page.insert_text((220, 180), "Abstract", fontsize=12)
        pdf.save(path)
    doc = VerifiedDocument(path, hashlib.sha256(path.read_bytes()).hexdigest(), 1)
    header = capture_pdf_header(doc, TITLE)
    assert header.status == "ready"
    assert header.version == "pdf-header-v2"
    with pymupdf.open(path) as pdf:
        text = pdf[0].get_text(clip=pymupdf.Rect(header.crop))
    assert all(name in text for name in ("Alice Smith", "Bob Jones", "Carol White"))
    assert "Abstract" not in text


@pytest.mark.parametrize(
    "authors,author_y,title",
    [(False, 125, TITLE), (True, 350, TITLE), (True, 125, "A completely different scientific paper title")],
)
def test_uncertain_or_tall_header_is_optional(tmp_path, authors, author_y, title):
    header = capture_pdf_header(make_pdf(tmp_path, authors=authors, author_y=author_y), title)
    assert header.status == "skipped" and not header.png_base64


def test_missing_pdf_and_wrong_identity_are_not_success(tmp_path):
    doc = make_pdf(tmp_path)
    assert capture_pdf_header(VerifiedDocument(doc.path, "0" * 64, 1), TITLE).status == "failed"
    doc.path.unlink()
    assert capture_pdf_header(doc, TITLE).status == "failed"


def test_header_renders_in_both_channels_without_becoming_research_figure(tmp_path):
    header = capture_pdf_header(make_pdf(tmp_path), TITLE)
    web, wechat = render_article("报道", "导语\n正文", [], [], paper_header=header)
    for rendered in (web, wechat):
        assert rendered.index("导语") < rendered.index("data-paper-header") < rendered.index("正文")
        assert header.png_base64 in rendered
        assert "max-width:560px" in rendered
    assert EmbeddedImages(web).images == []
    skipped = header.model_copy(update={"status": "skipped"})
    assert "data-paper-header" not in render_article("报道", "正文", [], [], paper_header=skipped)[0]
    tampered = header.model_copy(update={"image_sha256": "0" * 64})
    with pytest.raises(ValueError, match="指纹"):
        render_article("报道", "正文", [], [], paper_header=tampered)


def test_legacy_presentation_does_not_change_serialization():
    value = ArticlePresentation(wechat_html="", wechat_hash="", source_body_hash="")
    assert "paper_header" not in value.model_dump()


@pytest.mark.parametrize(
    "body,before,after",
    [
        ("开篇第一段\n开篇第二段\n## 正文小标题\n后续内容", "开篇第二段", "正文小标题"),
        ("## 开篇标题\n首段内容\n第二段内容", "首段内容", "第二段内容"),
        ("首段内容\n第二段内容", "首段内容", "第二段内容"),
    ],
)
def test_header_keeps_opening_paragraphs_and_heading_attached(tmp_path, body, before, after):
    header = capture_pdf_header(make_pdf(tmp_path), TITLE)
    for rendered in render_article("报道", body, [], [], paper_header=header):
        assert rendered.index(before) < rendered.index("data-paper-header") < rendered.index(after)
