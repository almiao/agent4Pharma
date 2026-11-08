"""模型上下文不携带论文正文，包括候选和历史稿件内嵌的来源快照。"""

from agent4pharma.modules.content.draft_contracts import (
    ClaimEvidence,
    DraftRevision,
    DraftSubmission,
    ResearchEvidence,
)
from agent4pharma.workflows.production_dependencies import CandidateSnapshot, ProductionContext
from agent4pharma.workflows.production_reading_view import production_reading_view


def test_nested_paper_text_and_all_quotes_are_not_in_initial_context():
    raw = "UNREQUESTED_PAPER_BODY_SENTINEL"
    evidence = ResearchEvidence(id="page", title="PDF", text=raw * 1000, origin="source", fulltext=True)
    claim = "这项研究有明确的证据范围。"
    draft = DraftRevision(
        id="draft",
        story_id="story",
        brief_version=1,
        number=1,
        author_id="author",
        content_hash="hash",
        created_at="2026-11-01T00:00:00Z",
        evidence=[evidence],
        submission=DraftSubmission(
            title="研究",
            body=claim + "说明" * 50,
            summary="摘要",
            coverage="逐项回答本次任务书问题。",
            claims=[ClaimEvidence(claim=claim, evidence_id=evidence.id, quote=raw)],
        ),
    )
    context = ProductionContext(
        evidence=[evidence],
        draft=draft,
        candidates=[
            CandidateSnapshot(
                id="candidate",
                assessment_id="assessment",
                summary="已生成摘要",
                reason="理由",
                evidence=[evidence],
            )
        ],
    )
    view = production_reading_view(context, 20)
    assert raw not in view.model_dump_json()
    assert raw in context.draft.evidence[0].text
    assert view.evidence[0].characters == len(evidence.text)
    assert "quote" not in view.draft.submission.claims[0].model_dump()
