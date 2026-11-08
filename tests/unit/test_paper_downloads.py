import pytest

from agent4pharma.infrastructure.connectors.paper_identity import paper_identifier
from agent4pharma.infrastructure.persistence.paper_download_repository import PaperDownloadRepository
from agent4pharma.modules.materials.paper_downloads import PaperDownloadRequest, PaperDownloadResult


def request():
    return PaperDownloadRequest(
        batch_id="batch",
        decision_id="decision",
        material_ref="material",
        source_id="source",
        source_record_id="record",
        title="Paper",
        journal="Journal",
        url="https://doi.org/10.1234/test",
        identifier="10.1234/test",
    )


def test_immutable_attempts_latest_results_and_batch_isolation(tmp_path):
    repo = PaperDownloadRepository(tmp_path)
    first = PaperDownloadResult(
        id="one", request=request(), status="failed", created_at="2026-10-30T00:00:00Z"
    )
    repo.save(first)
    repo.save(first)
    with pytest.raises(ValueError):
        repo.save(first.model_copy(update={"status": "downloaded"}))
    latest = first.model_copy(
        update={"id": "two", "status": "downloaded", "created_at": "2026-10-30T01:00:00Z"}
    )
    repo.save(latest)
    assert repo.results("batch") == [latest]
    assert repo.results("other-batch") == []
    assert len(list((tmp_path / "paper-downloads/attempts").glob("*.json"))) == 2


def test_identifiers_do_not_invent_doi_from_pii_or_title():
    assert paper_identifier("https://example.org/doi/full/10.1234/test?rss=true") == "10.1234/test"
    assert paper_identifier("https://arxiv.org/abs/2601.12345v2") == "2601.12345v2"
    assert paper_identifier("https://www.sciencedirect.com/science/article/pii/S123456") == ""
    assert (
        paper_identifier("https://www.nature.com/articles/s41592-026-03270-z") == "10.1038/s41592-026-03270-z"
    )
    assert paper_identifier("https://evilnature.com/articles/pretend") == ""


def test_invalid_pdf_is_recorded_as_format_failure(tmp_path):
    from agent4pharma.infrastructure.web.paper_pdf_validation import validate_pdf

    path = tmp_path / "not-a-paper.pdf"
    path.write_bytes(b"%PDF-broken")
    assert validate_pdf(path, "10.1038/test", "Paper")["status"] == "invalid_pdf"
