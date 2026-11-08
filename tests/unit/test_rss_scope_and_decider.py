import json

import httpx
import pytest

from agent4pharma.infrastructure.llm.local_decider import LocalDecider
from agent4pharma.infrastructure.web.primary_reading_scope import primary_scope
from agent4pharma.modules.discovery.triage_contracts import TriageInput, TriagePolicy


def test_primary_scope_does_not_promote_preview_or_truncation():
    article = (
        "<article><h2>Main</h2>" + "Observed result. " * 250 + "<h2>Methods</h2>Actual method.</article>"
    )
    assert primary_scope(article.encode(), "text/html") == "primary_fulltext"
    assert primary_scope((article + '<div class="paywall"></div>').encode(), "text/html") == "primary_page"
    assert primary_scope(b"<article><h2>Abstract</h2>Short abstract</article>", "text/html") == "primary_page"
    assert primary_scope(article.encode(), "application/pdf") == "primary_page"


def test_loopback_only_and_model_revision_validation(monkeypatch):
    with pytest.raises(ValueError, match="回环"):
        LocalDecider("https://example.org", "revision")
    item = TriageInput(material_ref="m", source_record_id="r", source_id="s", title="T", journal="J", url="")
    payload = {
        "identity": {
            "model": "perplexity-ai/pplx-decider-flash-v1.2",
            "revision": "wrong",
            "device": "GPU",
            "attention": "noncausal_full_attention",
        },
        "responses": [],
    }

    def post(self, url, **kwargs):
        assert self._trust_env is False
        assert kwargs["json"]["requests"][0]["questions"]["route"]["criteria"] == TriagePolicy().criteria
        return httpx.Response(200, content=json.dumps(payload).encode(), request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", post)
    with pytest.raises(ValueError, match="版本"):
        LocalDecider("http://127.0.0.1:8895", "revision").classify([item], TriagePolicy())


@pytest.mark.parametrize(
    "margins,revision,valid",
    [
        ([0.1], "revision", True),
        ([], "revision", False),
        ([0.1], "wrong", False),
        ([float("inf")], "revision", False),
    ],
)
def test_native_ranking_checks_identity_cardinality_and_finite_values(monkeypatch, margins, revision, valid):
    from types import SimpleNamespace

    from pydantic import ValidationError

    import agent4pharma.infrastructure.llm.local_decider as module

    sent = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def post(self, url, json):
            sent.append((url, json))
            payload = {
                "identity": {
                    "model": "perplexity-ai/pplx-decider-flash-v1.2",
                    "revision": revision,
                    "device": "offline",
                    "attention": "noncausal_full_attention",
                },
                "margins": margins,
            }
            return SimpleNamespace(raise_for_status=lambda: None, content=__import__("json").dumps(payload))

    monkeypatch.setattr(module.httpx, "Client", Client)
    model = LocalDecider("http://127.0.0.1:8895", "revision")
    item = TriageInput(
        material_ref="m", source_record_id="r", source_id="s", title="Title", journal="Cell", url=""
    )
    if valid:
        assert model.rank([item], "Editorial priority") == [0.1]
        assert sent[0][0].endswith("/rank")
        assert sent[0][1]["requests"][0]["questions"]["priority"]["type"] == "noul"
    else:
        with pytest.raises((ValueError, ValidationError)):
            model.rank([item], "Editorial priority")
