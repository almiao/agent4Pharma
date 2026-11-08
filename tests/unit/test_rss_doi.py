import pytest

from agent4pharma.infrastructure.connectors.rss_feed import parse_feed


@pytest.mark.parametrize(
    "identifier",
    [
        "10.1016/j.ccell.2026.07.010",
        "doi:10.1016/j.ccell.2026.07.010",
        "https://doi.org/10.1016/j.ccell.2026.07.010",
    ],
)
def test_cell_dublin_core_identifier_supplies_doi(identifier):
    raw = f"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
        <item><title>UniCure</title><link>https://www.cell.com/cancer-cell/fulltext/example</link>
        <dc:identifier>ISSN:1535-6108</dc:identifier>
        <dc:identifier>{identifier}</dc:identifier>
        <dc:identifier>https://example.org/article</dc:identifier></item></rdf:RDF>""".encode()
    row = parse_feed(raw, "https://www.cell.com/cancer-cell/current.rss", "cell")[0]
    assert row.identifiers == {"doi": "10.1016/j.ccell.2026.07.010"}


@pytest.mark.parametrize("identifier", ["ISSN:1535-6108", "S153561082600348X", "unrelated 10.1234/a"])
def test_non_doi_identifier_is_not_promoted(identifier):
    raw = f"""<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item>
        <link>https://example.org/article</link><dc:identifier>{identifier}</dc:identifier>
        <description>Reference DOI: 10.1234/cited</description></item></channel></rss>""".encode()
    assert parse_feed(raw, "https://example.org/feed", "test")[0].identifiers == {}


def test_explicit_doi_takes_precedence_over_identifier():
    raw = b"""<rss xmlns:dc="http://purl.org/dc/elements/1.1/"
        xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/"><channel><item>
        <link>https://example.org/article</link><prism:doi>10.1234/primary</prism:doi>
        <dc:identifier>10.1234/other</dc:identifier></item></channel></rss>"""
    assert parse_feed(raw, "https://example.org/feed", "test")[0].identifiers == {"doi": "10.1234/primary"}
