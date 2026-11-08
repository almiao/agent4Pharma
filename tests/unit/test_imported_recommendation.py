import pytest

from agent4pharma.modules.discovery.intelligence_contracts import (
    IntelligenceEvidence,
    IntelligencePolicy,
    IntelligencePolicyVersion,
    IntelligenceRequest,
)
from agent4pharma.modules.discovery.news_selection import NewsComparisonCard, NewsComparisonResult
from agent4pharma.modules.discovery.paper_selection import PaperSelectionItem, PaperSelectionRun
from agent4pharma.modules.materials.paper_downloads import PaperDownloadResult
from agent4pharma.workflows.imported_paper_recommendation import (
    confirm_imported_recommendation,
    imported_recommendation_candidate,
)
from agent4pharma.workflows.production_dependencies import ProductionContext
from agent4pharma.workflows.production_reading_view import production_reading_view
from agent4pharma.workflows.selected_paper_download import download_request


@pytest.fixture
def imported():
    policy = IntelligencePolicyVersion(
        id="p",
        version=1,
        status="published",
        content_hash="hash",
        definition=IntelligencePolicy(instructions="Offline screening instructions " * 3),
    )
    item = PaperSelectionItem(
        state="shortlisted",
        rank=1,
        download_id="pdf-1",
        request=IntelligenceRequest(
            material_ref="m",
            model="offline",
            policy=policy,
            evidence=[IntelligenceEvidence(id="record", title="Biology model", text="Source abstract")],
        ),
    )
    run = PaperSelectionRun(
        id="selection-" + "b" * 24,
        batch_id="batch",
        phase="fulltexts",
        created_at="2026-11-02T00:00:00Z",
        items=[item],
    )
    result = PaperDownloadResult(
        id="pdf-1",
        request=download_request(run, item),
        status="downloaded",
        identity_match=True,
        pages=2,
        sha256="a" * 64,
        created_at=run.created_at,
    )
    return run, result


def test_confirmation_is_idempotent_and_does_not_fabricate_assessment_or_reading(imported):
    run, result = imported
    with pytest.raises(ValueError, match="尚未人工"):
        imported_recommendation_candidate(run, "record", result)
    before = run.items[0].model_dump(exclude={"human_recommendation"})
    confirmation = confirm_imported_recommendation(run, "record", result)
    assert confirm_imported_recommendation(run, "record", result) == confirmation
    assert run.items[0].model_dump(exclude={"human_recommendation"}) == before
    candidate = imported_recommendation_candidate(run, "record", result)
    assert candidate.id == confirmation.id and candidate.assessment_id == ""
    assert not candidate.evidence[0].fulltext
    assert candidate.evidence[0].document.sha256 == result.sha256
    assert candidate.evidence[0].text == "Source abstract"
    assert run.items[0].fulltext_task_id == ""


@pytest.mark.parametrize(
    "change",
    [
        {"sha256": "b" * 64},
        {"id": "another-pdf"},
        {"identity_match": False},
        {"pages": 0},
    ],
)
def test_changed_or_unverified_document_cannot_reuse_confirmation(imported, change):
    run, result = imported
    confirm_imported_recommendation(run, "record", result)
    with pytest.raises(ValueError):
        imported_recommendation_candidate(run, "record", result.model_copy(update=change))
    with pytest.raises(ValueError):
        confirm_imported_recommendation(run, "record", result.model_copy(update=change))


def test_new_production_input_uses_structured_basis_without_old_research_checklist(imported):
    run, document = imported
    run.items[0].selection_reason = "旧拼接文本：人工确认推荐。全文待核实：实验重复？"
    run.comparison_result = NewsComparisonResult(
        selected_ids=["record"],
        cards=[
            NewsComparisonCard(
                record_id="record",
                verdict="recommend",
                relevance=3,
                news_hook="具体新闻进展",
                reason="同轮比较的入选理由",
                evidence_quote="Source abstract",
                ai_quote="Source",
                domain_quote="abstract",
                institution_quote="",
                verification_question="实验对照与重复？",
            )
        ],
    )
    confirm_imported_recommendation(run, "record", document)
    before = run.model_dump_json()
    candidate = imported_recommendation_candidate(run, "record", document)
    context = ProductionContext(candidates=[candidate])
    assert candidate.reason == ""
    assert candidate.selection_basis.news_hook == "具体新闻进展"
    assert candidate.selection_basis.reason == "同轮比较的入选理由"
    assert "全文待核实" not in context.model_dump_json()
    assert "实验对照与重复" not in context.model_dump_json()
    assert "confirmed_at" not in context.model_dump_json()
    view = production_reading_view(context, 5)
    assert view.candidates[0].selection_basis == candidate.selection_basis
    assert run.model_dump_json() == before


def test_missing_comparison_does_not_import_old_flat_reason_or_change_frozen_identity(imported):
    run, document = imported
    run.items[0].selection_reason = "全文待核实：旧问题"
    confirm_imported_recommendation(run, "record", document)
    candidate = imported_recommendation_candidate(run, "record", document)
    assert candidate.reason == "" and candidate.selection_basis is None
    legacy = candidate.model_dump()
    legacy["reason"] = "历史依据原文"
    context = ProductionContext.model_validate({"candidates": [legacy]})
    assert "selection_basis" not in context.model_dump_json()
    assert context.candidates[0].model_dump() == legacy


def test_unfinished_paused_and_historical_results_are_not_silently_recommended(imported):
    run, result = imported
    run.items[0].rank = 10
    run.phase = "abstracts"
    with pytest.raises(ValueError, match="筛选完成"):
        confirm_imported_recommendation(run, "record", result)
    run.phase = "fulltexts"
    run.paused = True
    with pytest.raises(ValueError, match="暂停"):
        confirm_imported_recommendation(run, "record", result)
    run.paused = False
    run.items[0].continuation_status = "not_recommended"
    with pytest.raises(ValueError, match="历史"):
        confirm_imported_recommendation(run, "record", result)
    assert run.items[0].human_recommendation is None
