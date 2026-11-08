from agent4pharma.infrastructure.connectors.rss_feed import parse_feed
from agent4pharma.infrastructure.web.article_parser import parse_document
from agent4pharma.modules.maintenance.repository import FetchedDocument


def test_nature_encoded_summary_is_available_to_agent_without_faking_time():
    raw = b"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"
        xmlns:content="http://purl.org/rss/1.0/modules/content/"
        xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">
        <item><title>Protein engineering</title><link>https://www.nature.com/articles/example</link>
        <content:encoded><![CDATA[<p>A generative model designs proteins.</p>
        <script>untrusted()</script>]]></content:encoded>
        <dc:creator>Alice</dc:creator><dc:creator>Bob</dc:creator>
        <dc:date>2026-10-29</dc:date><prism:doi>10.0000/example</prism:doi></item></rdf:RDF>"""
    url = "https://www.nature.com/nature.rss"
    row = parse_feed(raw, url, "nature")[0]
    assert row.abstract == "A generative model designs proteins."
    assert row.authors == ["Alice", "Bob"]
    assert row.identifiers == {"doi": "10.0000/example"}
    assert row.published is None
    assert row.metadata["date_raw"] == "2026-10-29"
    assert row.metadata["feed_parser_version"] == "rss-v4"
    page = parse_document(FetchedDocument(url, raw, "application/rss+xml", 200), "job", "page", url)
    assert "A generative model designs proteins." in page.text
    assert "untrusted()" not in page.text
    assert len(page.feed_entries) == 1


def test_existing_description_takes_precedence_over_encoded_fallback():
    raw = b"""<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item>
        <title>Existing feed</title><link>https://example.org/a</link>
        <description>Existing abstract</description><content:encoded>Different body</content:encoded>
        <pubDate>2026-10-29T12:00:00Z</pubDate></item></channel></rss>"""
    row = parse_feed(raw, "https://example.org/feed", "example")[0]
    assert row.abstract == "Existing abstract"
    assert row.published == "2026-10-29T12:00:00+00:00"
