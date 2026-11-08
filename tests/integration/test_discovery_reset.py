"""全部重跑从无资料、缓存和任务的新库开始，来源规则和旧备份仍完整。"""

import sqlite3
from contextlib import closing

import pytest

from agent4pharma.infrastructure.persistence.sqlite.application_storage import SqliteApplicationStorage
from agent4pharma.infrastructure.persistence.sqlite.archive_batch_repository import ArchiveBatchRepository
from agent4pharma.infrastructure.persistence.sqlite.discovery_reset_repository import DiscoveryResetRepository
from agent4pharma.modules.materials.collection_records import CollectedRecord
from agent4pharma.runtime.settings import ApplicationSettings


def test_reset_keeps_configuration_but_not_materials_jobs_or_cached_decisions(tmp_path):
    root = tmp_path / "old"
    store = SqliteApplicationStorage(root)
    config = ApplicationSettings(data_dir=root, sources={"arxiv": {"query": "biology"}})
    store.seed_sources(config)
    _, batch = store.create_run("2026-10-30", config)
    with store.db:
        record = store.materials.ingest_record(CollectedRecord("arxiv", "paper", "AI biology"), [])
        store.discovery.register_record(batch, record, config.snapshot())
        store.db.execute("UPDATE source_access_configs SET next_request_at=999999")
    snapshot = tmp_path / "backup.sqlite3"
    ArchiveBatchRepository(root).backup(snapshot)
    destination = tmp_path / "fresh" / "discovery.sqlite3"
    result = DiscoveryResetRepository(snapshot).build_clean_database(destination)
    assert next(r.rows for r in result if r.name == "sources") == 1
    with closing(sqlite3.connect(destination)) as db:
        assert db.execute("SELECT count(*) FROM sources").fetchone()[0] == 1
        assert db.execute("SELECT next_request_at FROM source_access_configs").fetchone()[0] == 0
        for table in (
            "materials",
            "source_records",
            "jobs",
            "discovery_batches",
            "discovery_batch_items",
            "rss_triage_decisions",
            "intelligence_tasks",
            "production_tasks",
        ):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert store.db.execute("SELECT count(*) FROM source_records").fetchone()[0] == 1
    with pytest.raises(FileExistsError):
        DiscoveryResetRepository(snapshot).build_clean_database(destination)
    store.close()
