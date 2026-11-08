import json
from datetime import datetime, timezone

import httpx
import pytest

from agent4pharma.infrastructure.connectors.arxiv import Arxiv
from agent4pharma.infrastructure.connectors.pubmed import PubMed
from agent4pharma.infrastructure.connectors.rxiv import Rxiv
from agent4pharma.infrastructure.connectors.transport import Transport, safe_url
from agent4pharma.infrastructure.persistence.sqlite.application_storage import SqliteApplicationStorage
from agent4pharma.modules.collection.errors import SourceError
from agent4pharma.modules.materials.collection_records import SourceResponse
from agent4pharma.runtime.settings import ApplicationSettings

START = datetime(2026, 10, 27, tzinfo=timezone.utc)
END = datetime(2026, 10, 28, tzinfo=timezone.utc)


class Responses:
    def __init__(self, *bodies):
        self.bodies = iter(bodies)
        self.requests = []

    def get(self, source, url, params=None):
        self.requests.append((source, url, params))
        body = next(self.bodies)
        return SourceResponse(body.encode(), url, "test")


def test_pubmed_snapshots_ids_and_batch_fetch():
    search = json.dumps({"esearchresult": {"count": "1", "idlist": ["123"]}})
    fetch = """<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article>
    <ArticleTitle>A <i>protein</i> study</ArticleTitle><Abstract><AbstractText>Findings</AbstractText>
    </Abstract><AuthorList><Author><LastName>Smith</LastName><AffiliationInfo>
    <Affiliation>Biology Department, Research Institute</Affiliation>
    </AffiliationInfo></Author></AuthorList></Article></MedlineCitation><PubmedData><ArticleIdList>
    <ArticleId IdType="doi">10.1/A</ArticleId>
    </ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>"""
    client = Responses(search, fetch)
    page = PubMed(client).page(START, END, "AI", None)
    assert page.records[0].title == "A protein study"
    assert page.records[0].identifiers["doi"] == "10.1/A"
    assert page.records[0].metadata["affiliations"] == ["Biology Department, Research Institute"]
    assert page.next_cursor is None
    assert len(client.requests) == 2
    assert '"2026/10/27"[EDAT] : "2026/10/27"[EDAT]' in client.requests[0][2]["term"]


def test_pubmed_truncation_fails_visibly():
    response = json.dumps({"esearchresult": {"count": "10000", "idlist": []}})
    with pytest.raises(SourceError, match="10000"):
        PubMed(Responses(response)).page(START, END, "", None)


def test_arxiv_preserves_preprint_identity():
    body = """<feed xmlns="http://www.w3.org/2005/Atom"
    xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom">
    <opensearch:totalResults>1</opensearch:totalResults><entry>
    <id>http://arxiv.org/abs/2610.12345v2</id><title>Paper</title><summary>Abstract</summary>
    <arxiv:doi>10.1/journal</arxiv:doi></entry></feed>"""
    page = Arxiv(Responses(body)).page(START, END, "", None)
    assert page.records[0].version == "2"
    assert page.records[0].identifiers == {"arxiv": "2610.12345"}
    assert page.records[0].metadata["related_journal_doi"] == "10.1/journal"


def test_rxiv_paging_and_empty_page_failure():
    client = Responses(
        json.dumps(
            {
                "messages": [{"status": "ok", "total": 31}],
                "collection": [{"doi": f"10.1/{i}", "title": "Paper", "version": 1} for i in range(30)],
            }
        )
    )
    page = Rxiv("medrxiv", client).page(START, END, "", None)
    assert page.next_cursor == {"offset": 30, "total": 31}
    assert "/medrxiv/" in client.requests[0][1]
    assert "/2026-10-27/2026-10-27/" in client.requests[0][1]
    with pytest.raises(SourceError, match="empty page"):
        Rxiv(
            "biorxiv",
            Responses(
                json.dumps(
                    {
                        "messages": [{"status": "ok", "total": 1}],
                        "collection": [],
                    }
                )
            ),
        ).page(START, END, "", None)


def test_retry_limit_and_no_secret_leak(tmp_path, monkeypatch):
    monkeypatch.setenv("NCBI_API_KEY", "test-secret")
    store = SqliteApplicationStorage(tmp_path)
    store.seed_sources(ApplicationSettings(sources={"pubmed": {}}))
    calls = []
    sleeps = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "2"})

    transport = Transport(store, httpx.Client(transport=httpx.MockTransport(handler)), sleep=sleeps.append)
    try:
        with pytest.raises(SourceError) as error:
            transport.get("pubmed", "https://example.org", {"api_key": "test-secret"})
        assert "test-secret" not in str(error.value)
        assert len(calls) == 4
        assert 2 in sleeps
        assert "test-secret" not in safe_url("https://example.org?api_key=test-secret&x=1")
    finally:
        transport.close()
        store.close()


def test_long_retry_after_is_persisted_not_slept(tmp_path):
    store = SqliteApplicationStorage(tmp_path)
    store.seed_sources(ApplicationSettings(sources={"pubmed": {}}))
    calls, sleeps = [], []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "3600"})

    transport = Transport(store, httpx.Client(transport=httpx.MockTransport(handler)), sleep=sleeps.append)
    try:
        with pytest.raises(SourceError, match="retry deferred"):
            transport.get("pubmed", "https://example.org")
        with pytest.raises(SourceError, match="access budget deferred"):
            transport.get("pubmed", "https://example.org")
        assert len(calls) == 1
        assert all(delay < 60 for delay in sleeps)
    finally:
        transport.close()
        store.close()
