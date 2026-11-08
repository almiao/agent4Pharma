import json

import httpx
import pytest

from agent4pharma.infrastructure.llm.news_comparison import CommercialNewsComparison
from agent4pharma.modules.discovery.news_selection import (
    NEWS_COMPARISON_INSTRUCTIONS,
    NEWS_COMPARISON_OUTPUT_CONTRACT,
    NewsComparisonCard,
    NewsComparisonPaper,
    NewsComparisonRequest,
    NewsComparisonResult,
    validate_news_comparison,
)


def request():
    return NewsComparisonRequest(
        instructions=NEWS_COMPARISON_INSTRUCTIONS,
        model="offline",
        endpoint="https://model.invalid/v1",
        max_tokens=4096,
        thinking_mode="disabled",
        limit=1,
        papers=[
            NewsComparisonPaper(
                record_id="r1",
                title="AI biology",
                text="measured experimental result",
                journal="Nature",
                priority_journal=True,
                local_rank=1,
                affiliations=["Known Research Institute"],
            )
        ],
    )


def result():
    return NewsComparisonResult(
        selected_ids=["r1"],
        cards=[
            NewsComparisonCard(
                record_id="r1",
                verdict="recommend",
                relevance=3,
                news_hook="具体研究进展",
                reason="值得核实的新闻点",
                evidence_quote="experimental result",
                ai_quote="AI",
                domain_quote="biology",
                institution_quote="Known Research Institute",
                verification_question="实验对照是什么？",
            )
        ],
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("evidence_quote", "fabricated result"),
        ("institution_quote", "Unknown Big Company"),
        ("ai_quote", ""),
        ("relevance", 1),
    ],
)
def test_news_comparison_rejects_unbacked_recommendation(field, value):
    decision = result()
    setattr(decision.cards[0], field, value)
    with pytest.raises(ValueError):
        validate_news_comparison(request(), decision)


def test_news_comparison_requires_full_pool_and_no_duplicates():
    validate_news_comparison(request(), result())
    decision = result()
    decision.cards.append(decision.cards[0])
    with pytest.raises(ValueError, match="逐篇覆盖"):
        validate_news_comparison(request(), decision)
    decision = result()
    decision.selected_ids *= 2
    with pytest.raises(ValueError, match="重复"):
        validate_news_comparison(request(), decision)


def test_recommendation_needs_no_verification_question_and_preserves_legacy_card():
    old = result().model_dump()
    new = result().model_dump()
    new["cards"][0].pop("verification_question")
    parsed = NewsComparisonResult.model_validate(new)
    validate_news_comparison(request(), parsed)
    assert parsed.cards[0].verification_question == ""
    assert NewsComparisonResult.model_validate(old).model_dump() == old


def test_valuable_paper_may_be_deferred_without_becoming_an_extra_download():
    decision = result()
    decision.selected_ids = []
    validate_news_comparison(request(), decision)
    decision.cards[0].verdict = "exclude"
    decision.selected_ids = ["r1"]
    with pytest.raises(ValueError, match="推荐判断一致"):
        validate_news_comparison(request(), decision)


@pytest.mark.parametrize(
    "mode",
    [
        "ok",
        "malformed",
        "truncated",
        "http_error",
        "unknown_id",
        "duplicate_selected",
        "unknown_quote",
        "literal_quote",
    ],
)
@pytest.mark.parametrize("thinking_mode", ["disabled", "provider_default"])
@pytest.mark.parametrize("compact", [False, True])
def test_commercial_adapter_is_bounded_json_only_and_sanitizes_errors(
    monkeypatch, mode, thinking_mode, compact
):
    real_client = httpx.Client
    calls = []

    def respond(call):
        calls.append(call)
        body = json.loads(call.content)
        assert call.url == "https://model.invalid/v1/chat/completions"
        assert body["model"] == "offline" and body["max_tokens"] == 4096
        if thinking_mode == "disabled":
            assert body["thinking"] == {"type": "disabled"}
        else:
            assert "thinking" not in body
        assert "tools" not in body
        assert len(json.loads(body["messages"][1]["content"])["papers"]) == 1
        assert json.loads(body["messages"][1]["content"])["required_card_ids"] == ["p1"]
        paper = json.loads(body["messages"][1]["content"])["papers"][0]
        assert paper["record_id"] == "p1"
        assert ("p1.a1.1" in paper["quote_spans"]) is not compact
        assert ("ai_quote" in body["messages"][0]["content"]) is not compact
        if mode == "http_error":
            return httpx.Response(503, text="sensitive response")
        wire_result = result()
        wire_result.selected_ids = ["p1"]
        wire_result.cards[0].record_id = "p1"
        wire_result.cards[0].evidence_quote = "p1.s.1"
        wire_result.cards[0].ai_quote = "p1.t.1"
        wire_result.cards[0].domain_quote = "p1.t.1"
        wire_result.cards[0].institution_quote = "p1.a1.1"
        if mode == "unknown_quote":
            wire_result.cards[0].evidence_quote = "p2.s.1"
        if mode == "literal_quote":
            wire_result.cards[0].evidence_quote = "experimental result"
        if mode == "unknown_id":
            wire_result.selected_ids = ["p999"]
        if mode == "duplicate_selected":
            wire_result.selected_ids = ["p1", "p1"]
        payload = wire_result.model_dump()
        if compact:
            for field in ("ai_quote", "domain_quote", "institution_quote", "verification_question"):
                del payload["cards"][0][field]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length" if mode == "truncated" else "stop",
                        "message": {
                            "content": "broken secret" if mode == "malformed" else json.dumps(payload)
                        },
                    }
                ]
            },
        )

    def client(**options):
        assert options["trust_env"] is False and options["timeout"] <= 180
        return real_client(transport=httpx.MockTransport(respond), **options)

    monkeypatch.setattr("agent4pharma.infrastructure.llm.news_comparison.httpx.Client", client)
    model = CommercialNewsComparison("offline-test-key")
    frozen = request().model_copy(update={"thinking_mode": thinking_mode})
    if compact:
        frozen.instructions += NEWS_COMPARISON_OUTPUT_CONTRACT
    if mode == "ok":
        expected = result()
        expected.cards[0].evidence_quote = "measured experimental result"
        expected.cards[0].ai_quote = expected.cards[0].domain_quote = "AI biology"
        if compact:
            expected.cards[0].ai_quote = expected.cards[0].domain_quote = ""
            expected.cards[0].institution_quote = expected.cards[0].verification_question = ""
        actual = model.compare(frozen)
        assert actual == expected
        validate_news_comparison(frozen, actual)
        if compact:
            actual.cards[0].evidence_quote = ""
            with pytest.raises(ValueError, match="推荐引用"):
                validate_news_comparison(frozen, actual)
    elif mode == "duplicate_selected":
        duplicate = model.compare(frozen)
        assert duplicate.selected_ids == ["r1", "r1"]
        with pytest.raises(ValueError, match="重复"):
            validate_news_comparison(frozen, duplicate)
    else:
        with pytest.raises((ValueError, RuntimeError)) as failure:
            model.compare(frozen)
        assert not any(
            s in str(failure.value) for s in ["offline-test-key", "sensitive", "secret", "model.invalid"]
        )
    assert len(calls) == 1


def test_reference_chunks_are_exact_bounded_and_belong_to_each_paper():
    from agent4pharma.infrastructure.llm.news_comparison import _quote_spans

    paper = request().papers[0].model_copy(update={"text": "A long source sentence. " * 100})
    spans = _quote_spans("p1", paper)
    assert "".join(value for key, value in spans.items() if key.startswith("p1.s.")) == paper.text
    assert all(len(value) <= 600 for value in spans.values())
    assert set(spans).isdisjoint(_quote_spans("p2", paper))
