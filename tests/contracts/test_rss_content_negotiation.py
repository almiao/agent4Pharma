"""RSS 连接器在真实 HTTP 边界明确接受 XML，且非成功响应不能被当作空 Feed。"""

from datetime import datetime, timezone

import pytest

from agent4pharma.infrastructure.connectors.rss_feed import RssFeedConnector
from agent4pharma.infrastructure.web.public_fetch import PublicPageFetcher
from agent4pharma.modules.collection.errors import SourceError
from agent4pharma.modules.maintenance.repository import FetchedDocument


def test_rss_accepts_xml_without_changing_page_fetcher_and_preserves_failures(monkeypatch):
    connector = RssFeedConnector("journal", "https://example.org/rss")
    seen = []

    def response(self, url):
        seen.append(self._headers())
        return FetchedDocument(
            url,
            b"<rss><channel><item><title>Article</title><link>https://example.org/a</link></item></channel></rss>",
            "text/xml",
            200,
        )

    monkeypatch.setattr(PublicPageFetcher, "fetch", response)
    point = datetime.now(timezone.utc)
    assert len(connector.page(point, point, "", None).records) == 1
    assert seen[0]["Accept"].startswith("application/rss+xml")
    assert PublicPageFetcher._headers()["Accept"].startswith("text/html")
    monkeypatch.setattr(
        PublicPageFetcher, "fetch", lambda self, url: FetchedDocument(url, b"blocked", "text/html", 403)
    )
    with pytest.raises(SourceError, match="HTTP 403"):
        connector.page(point, point, "", None)
