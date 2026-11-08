"""引文由原始范围提取，过期、越界和冲突定位不能绕过引用校验。"""

import pytest

from agent4pharma.modules.content.draft_contracts import (
    ClaimEvidence,
    DraftSubmission,
    EvidenceSpan,
    ResearchEvidence,
    bind_draft_quotes,
    validate_draft,
)


def submission(source, **span):
    claim = "这个结果有明确的适用范围。"
    return DraftSubmission(
        title="研究",
        body=claim + "分析" * 50,
        summary="摘要",
        coverage="逐项覆盖本次任务要求。",
        claims=[
            ClaimEvidence(
                claim=claim,
                evidence_id=source.id,
                source_span=EvidenceSpan(content_hash=source.content_hash, start=0, end=12).model_copy(
                    update=span
                ),
            )
        ],
    )


def test_span_extracts_exact_text_and_preserves_legacy_serialization():
    source = ResearchEvidence(
        id="page",
        title="PDF",
        text="原文包含\n换行和  双空格。保持逐字一致。",
        origin="primary_fulltext_excerpt",
        fulltext=True,
    )
    draft = submission(source)
    bound = bind_draft_quotes(draft, [source])
    assert bound.claims[0].quote == source.text[:12] and not draft.claims[0].quote
    validate_draft(bound, [source], True)
    legacy = ClaimEvidence(claim="原句", evidence_id="page", quote="原文")
    assert "source_span" not in legacy.model_dump()


@pytest.mark.parametrize("span", [{"content_hash": "0" * 64}, {"start": 12, "end": 4}, {"end": 999}])
def test_bad_spans_fail_even_in_direct_validation(span):
    source = ResearchEvidence(
        id="page", title="PDF", text="Actual contiguous evidence here.", origin="source", fulltext=True
    )
    with pytest.raises(ValueError):
        validate_draft(submission(source, **span), [source], True)


def test_span_cannot_silently_correct_conflicting_quote():
    source = ResearchEvidence(
        id="page", title="PDF", text="Actual contiguous evidence here.", origin="source", fulltext=True
    )
    draft = submission(source)
    draft.claims[0].quote = "invented text"
    with pytest.raises(ValueError, match="不一致"):
        bind_draft_quotes(draft, [source])
