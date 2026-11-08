"""DSH 读取前重新校验已导入文件，不能越过账本目录和内容绑定。"""

import hashlib

import pytest

from agent4pharma.composition import build_bound_document_reader
from agent4pharma.infrastructure.persistence.paper_download_repository import PaperDownloadRepository
from agent4pharma.modules.materials.paper_downloads import PaperDownloadRequest, PaperDownloadResult


def test_mineru_reader_refuses_replaced_or_outside_file(tmp_path):
    directory = tmp_path / "paper-downloads" / "manual"
    directory.mkdir(parents=True)
    paper = directory / "main.pdf"
    paper.write_bytes(b"%PDF-fixture")
    request = PaperDownloadRequest(
        batch_id="batch",
        decision_id="selection:record",
        material_ref="material",
        source_id="source",
        source_record_id="record",
        title="Paper",
        journal="Journal",
        url="https://example.org/paper",
    )
    result = PaperDownloadResult(
        id="download",
        request=request,
        status="downloaded",
        file=str(paper),
        pages=1,
        sha256=hashlib.sha256(paper.read_bytes()).hexdigest(),
        identity_match=True,
        created_at="2026-11-01T00:00:00Z",
    )
    ledger = PaperDownloadRepository(tmp_path)
    ledger.save(result)
    reader = build_bound_document_reader(tmp_path, "download")
    paper.write_bytes(b"%PDF-changed")
    with pytest.raises(ValueError, match="文件已变化"):
        reader.read_document()
    with pytest.raises(ValueError, match="不存在"):
        build_bound_document_reader(tmp_path, "other")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-fixture")
    ledger.save(
        result.model_copy(
            update={"id": "outside", "file": str(outside), "created_at": "2026-11-02T00:00:00Z"}
        )
    )
    with pytest.raises(ValueError, match="文件已变化"):
        build_bound_document_reader(tmp_path, "outside")
