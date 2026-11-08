"""补采并入和清理：保留明确引用，不损坏真实证据链。"""

import json

import pytest
from conftest import seed_archived_task

from agent4pharma.composition import build_selection_policy
from agent4pharma.infrastructure.persistence.sqlite.application_storage import SqliteApplicationStorage
from agent4pharma.infrastructure.persistence.sqlite.archive_batch_repository import ArchiveBatchRepository
from agent4pharma.infrastructure.persistence.sqlite.discovery_run_view import DiscoveryRunReader
from agent4pharma.infrastructure.persistence.sqlite.intelligence_repository import IntelligenceRepository
from agent4pharma.infrastructure.persistence.sqlite.retained_batch_repository import RetainedBatchRepository
from agent4pharma.modules.discovery.intelligence_contracts import (
    IntelligenceEvidence,
    IntelligenceRequest,
)
from agent4pharma.modules.materials.collection_records import CollectedRecord
from agent4pharma.runtime.settings import ApplicationSettings


def ingest(store, version):
    return store.materials.ingest_record(
        CollectedRecord(source="arxiv", external_id="same", title="LLM protein", version=version), []
    )


def test_cleanup_keeps_current_versions_and_protected_batches(tmp_path):
    store = SqliteApplicationStorage(tmp_path)
    config = ApplicationSettings(data_dir=tmp_path)
    with store.db:
        old_record = ingest(store, "v1")
    _, old = store.create_run("2026-10-28", config)
    with store.db:
        store.discovery.register_record(old, old_record, config.snapshot())
        ingest(store, "v2")
    ArchiveBatchRepository(tmp_path).consolidate("active", [], config)
    repository = RetainedBatchRepository(tmp_path)
    result = repository.prune_history(["active"])
    assert result.batches == 1
    assert result.records == 1
    assert result.artifacts == 1
    assert result.obsolete_files
    assert result.retained_batches == ["active"]
    assert store.db.execute("SELECT count(*) FROM discovery_batch_items").fetchone()[0] == 1
    assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert repository.prune_history(["active"]).records == 0
    with pytest.raises(ValueError, match="保留批次不存在"):
        repository.prune_history(["typo"])
    store.close()


def test_merge_retains_failures_and_is_idempotent(tmp_path):
    store = SqliteApplicationStorage(tmp_path)
    config = ApplicationSettings(data_dir=tmp_path)
    ArchiveBatchRepository(tmp_path).consolidate("active", [], config)
    retry, _ = store.create_run("2026-10-30", config)
    job = store.source_job(retry, "arxiv", {"source": "arxiv"})
    store.set_job(job["id"], "failed", error="HTTP 403", attempt=True)
    with store.db:
        record = ingest(store, "v1")
        store.discovery.register_record(retry, record, config.snapshot())
    repository = RetainedBatchRepository(tmp_path)
    assert repository.merge_retry("active", retry) == 1
    assert repository.merge_retry("active", retry) == 0
    merged = store.job("active:arxiv")
    assert merged["status"] == "failed"
    assert merged["error"] == "HTTP 403"
    assert merged["request"]["origin_batch"] == retry
    reader = DiscoveryRunReader(tmp_path)
    assert reader.snapshot().selected == "active"
    assert reader.snapshot(retry).selected == retry
    assert reader.snapshot(retry).aggregate_batch == "active"
    assert reader.snapshot("active").batch_kind == "archive_import"
    result = repository.prune_history(["active", retry])
    assert result.batches == 0
    assert store.db.execute("SELECT count(*) FROM source_records").fetchone()[0] == 1
    store.close()


def test_production_reference_protects_older_batch(tmp_path):
    store = SqliteApplicationStorage(tmp_path)
    config = ApplicationSettings(data_dir=tmp_path)
    _, old = store.create_run("2026-10-28", config)
    ArchiveBatchRepository(tmp_path).consolidate("active", [], config)
    with store.db:
        store.db.execute(
            "INSERT INTO production_stories(id,revision,payload) VALUES ('story',1,?)",
            (json.dumps({"source_batch": old}),),
        )
    result = RetainedBatchRepository(tmp_path).prune_history(["active"])
    assert result.batches == 0
    assert old in result.retained_batches
    store.close()


def test_imported_assessment_keeps_original_parent_and_checks_identity(tmp_path):
    store = SqliteApplicationStorage(tmp_path)
    config = ApplicationSettings(data_dir=tmp_path)
    ArchiveBatchRepository(tmp_path).consolidate("active", [], config)
    retry, _ = store.create_run("2026-10-30", config)
    with store.db:
        record = ingest(store, "v1")
        store.discovery.register_record(retry, record, config.snapshot())
    intelligence = IntelligenceRepository(tmp_path)
    task = seed_archived_task(
        tmp_path,
        IntelligenceRequest(
            material_ref=record.material_id,
            mode="production",
            parent_ref=retry,
            evidence=[IntelligenceEvidence(id=record.record_id, text="LLM protein")],
            policy=build_selection_policy(tmp_path),
            model="offline",
        ),
    )
    RetainedBatchRepository(tmp_path).merge_retry("active", retry)
    summary = intelligence.run_summaries("active")
    assert len(summary) == 1
    assert summary[0].id == task.id
    assert summary[0].parent_ref == retry
    with store.db:
        store.db.execute("UPDATE discovery_batch_items SET material_id='unrelated' WHERE batch_id='active'")
    assert intelligence.run_summaries("active") == []
    assert intelligence.candidates("active") == []
    store.close()
