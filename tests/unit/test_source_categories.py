"""来源类别只使用明确元数据，并保留未知来源。"""

import pytest

from agent4pharma.modules.sources.source_categories import source_category


@pytest.mark.parametrize(
    ("identity", "metadata", "expected"),
    [
        ("pubmed", {}, "journal"),
        ("custom", {"source_kind": "journal_site"}, "journal"),
        ("custom", {"url": "https://www.biorxiv.org/content/123"}, "preprint"),
        ("arxiv", {}, "preprint"),
        ("custom", {"url": "https://biorxiv.org.example.com"}, "unknown"),
        ("custom", {"material_type": "news_report", "journal": "News feed"}, "news"),
        ("custom", {"material_type": "press_release"}, "announcement"),
        ("custom", {"material_type": "newsletter"}, "other"),
        ("custom", {"source_kind": "news_section"}, "news"),
        ("custom", {"source_kind": "press_release_platform"}, "announcement"),
        ("custom", {"source_kind": "filing_registry"}, "announcement"),
        ("custom", {"source_kind": "publisher_site"}, "other"),
        ("custom", {"source_kind": "website"}, "other"),
        ("custom", {"source_kind": "identifier_service"}, "other"),
        ("custom", {"source_kind": "conference_site"}, "other"),
        ("custom", {"source_kind": "repository_platform"}, "other"),
        ("custom", {}, "unknown"),
    ],
)
def test_classify_explicit_source_metadata(identity, metadata, expected):
    assert source_category(identity, **metadata) == expected
