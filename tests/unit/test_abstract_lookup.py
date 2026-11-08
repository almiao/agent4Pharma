"""摘要获取的离线契约测试：批量映射、倒排还原、占位清除与缺失语义。"""

from agent4pharma.infrastructure.connectors.abstract_lookup import (
    AbstractLookup,
    is_placeholder,
    normalize_doi,
    reconstruct_abstract,
)


class _Response:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, epmc=None, openalex=None, status=200):
        self.epmc = epmc or {"resultList": {"result": []}}
        self.openalex = openalex or {"results": []}
        self.status = status
        self.calls = []

    def get(self, url, params):
        self.calls.append((url, params))
        if "europepmc" in url:
            return _Response(self.epmc, self.status)
        return _Response(self.openalex, self.status)

    def close(self):
        pass


def test_normalize_doi_strips_resolver_prefix_and_case():
    assert normalize_doi("https://doi.org/10.1038/ABC-1") == "10.1038/abc-1"
    assert normalize_doi("") == ""


def test_placeholder_detection_matches_rss_stub():
    assert is_placeholder("")
    assert not is_placeholder("A short but real abstract.")
    stub = "Nature Genetics, Published online: 26 October 2026; doi:10.1038/s41588-026-02827-0"
    assert is_placeholder(stub)
    assert not is_placeholder("We report a " + "long abstract text " * 10)


def test_reconstruct_abstract_orders_words_by_position():
    index = {"b": [1], "a": [0], "c": [2]}
    assert reconstruct_abstract(index) == "a b c"
    assert reconstruct_abstract(None) == ""


def test_fetch_prefers_europepmc_and_falls_back_to_openalex():
    client = _Client(
        epmc={
            "resultList": {
                "result": [
                    {"doi": "10.1038/abc-1", "abstractText": "EPMC abstract"},
                ]
            }
        },
        openalex={
            "results": [
                {
                    "doi": "https://doi.org/10.1016/XYZ",
                    "abstract_inverted_index": {"Second": [1], "Fallback": [0]},
                }
            ]
        },
    )
    lookup = AbstractLookup(client=client, interval=0, sleep=lambda _: None)
    hits = lookup.fetch(["https://doi.org/10.1038/ABC-1", "10.1016/xyz", "10.1000/missing"])
    assert hits["10.1038/abc-1"].source == "europepmc"
    assert hits["10.1038/abc-1"].text == "EPMC abstract"
    assert hits["10.1016/xyz"].source == "openalex"
    assert hits["10.1016/xyz"].text == "Fallback Second"
    assert hits["10.1000/missing"].status == "none"


def test_upstream_failure_is_reported_as_error_not_success():
    client = _Client(status=503)
    lookup = AbstractLookup(client=client, interval=0, sleep=lambda _: None)
    hits = lookup.fetch(["10.1000/fail"])
    assert hits["10.1000/fail"].status == "error"


def test_source_abstract_markers_exclude_placeholders_and_unknown_feed_descriptions():
    from agent4pharma.infrastructure.connectors.source_abstracts import (
        feed_abstract_metadata,
        source_abstract_metadata,
    )

    assert source_abstract_metadata("", "pubmed_api", "Abstract/AbstractText") == {}
    placeholder = "Nature Genetics, Published online: 26 October 2026; doi:10.1038/example"
    assert feed_abstract_metadata(placeholder, "https://rss.arxiv.org/rss/q-bio", "description") == {}
    assert feed_abstract_metadata("News preview", "https://news.example/rss", "description") == {}
    assert source_abstract_metadata("Short genuine abstract.", "pubmed_api", "Abstract/AbstractText") == {
        "abstract_status": "found",
        "abstract_source": "pubmed_api",
        "abstract_field": "Abstract/AbstractText",
        "abstract_annotation_version": "source-abstract-v1",
    }
