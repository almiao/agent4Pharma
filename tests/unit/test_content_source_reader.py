"""网页补读只保存实际提取内容，错误状态不伪装成正文。"""

import pytest

from agent4pharma.infrastructure.web import content_reader
from agent4pharma.modules.maintenance.repository import FetchedDocument


def test_source_reader_preserves_retrieved_text_and_version(monkeypatch):
    document = FetchedDocument(
        "https://example.org/commentary",
        b"<title>Commentary</title><p>Independent opinion.</p>",
        "text/html",
        200,
    )
    monkeypatch.setattr(content_reader.PublicPageFetcher, "fetch", lambda self, url: document)
    first = content_reader.read_content_source("https://example.org/start")
    second = content_reader.read_content_source("https://example.org/start")
    assert first.id == second.id and first.content_hash == second.content_hash
    assert first.url == document.url and "Independent opinion." in first.text
    assert not first.fulltext and "不认证全文完整性" in first.read_scope


@pytest.mark.parametrize(
    "status,kind,body",
    [(403, "text/html", b"denied"), (200, "application/pdf", b"%PDF"), (200, "text/html", b"")],
)
def test_unavailable_source_is_not_evidence(monkeypatch, status, kind, body):
    monkeypatch.setattr(
        content_reader.PublicPageFetcher, "fetch", lambda self, url: FetchedDocument(url, body, kind, status)
    )
    with pytest.raises(ValueError, match="未取得"):
        content_reader.read_content_source("https://example.org/source")
