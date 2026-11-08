from agent4pharma.infrastructure.connectors.rss_feed import parse_feed


def test_rss_explicit_affiliations_preserve_fields_and_ignore_publisher():
    body = b"""<rss xmlns:author="https://example.org/author"><channel>
    <affiliation>Channel Institution</affiliation><item><title>AI biology</title>
    <link>https://example.org/paper</link><author:affiliation>DeepMind</author:affiliation>
    <author:affiliation>DeepMind</author:affiliation>
    <publisher><affiliation>Publisher Org</affiliation></publisher>
    <source><affiliation>Source Org</affiliation></source><creator>Smith</creator>
    </item></channel></rss>"""
    record = parse_feed(body, "https://example.org/rss", "test")[0]
    assert record.metadata["affiliations"] == ["DeepMind"]
    evidence = record.metadata["affiliation_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["origin"] == "rss_xml" and evidence[0]["field"].endswith("}affiliation")
    assert record.metadata["affiliations_version"] == "rss-affiliations-v1"


def test_atom_author_name_and_organization_are_separate():
    body = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:x="https://example.org/person"><entry>
    <title>AI biology</title><link href="https://example.org/paper"/>
    <author><name>Alice Smith</name><x:institution>Stanford University</x:institution></author>
    <author affiliation="Research Company"><name>Bob</name></author>
    <rights>Published by Famous Institute</rights></entry></feed>"""
    record = parse_feed(body, "https://example.org/rss", "test")[0]
    assert record.authors == ["Alice Smith", "Bob"]
    assert record.metadata["affiliations"] == ["Stanford University", "Research Company"]


def test_only_explicit_html_affiliations_are_extracted():
    body = b"""<rss><channel><item><title>AI biology</title><link>https://example.org/paper</link>
    <description><![CDATA[<p>Compared with work by DeepMind and Harvard.</p>
    <div class="affiliation">MIT <script>bad()</script></div>
    <span itemprop="affiliation">Arc Institute</span>]]></description></item></channel></rss>"""
    record = parse_feed(body, "https://example.org/rss", "test")[0]
    assert record.metadata["affiliations"] == ["MIT", "Arc Institute"]
    assert all(e["origin"] == "rss_html" for e in record.metadata["affiliation_evidence"])


def test_missing_affiliation_does_not_guess_from_author_publisher_or_summary():
    body = b"""<rss><channel><item><title>AI biology at Google DeepMind</title>
    <link>https://example.org/paper</link><creator>Harvard</creator><publisher>Google</publisher>
    <description>Microsoft compared with MIT work.</description></item></channel></rss>"""
    record = parse_feed(body, "https://example.org/rss", "test")[0]
    assert record.metadata["affiliations"] == []
    assert record.metadata["affiliation_evidence"] == []
