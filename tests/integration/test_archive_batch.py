"""归档汇批保留证据及历史，幂等、版本选择和损坏证据回滚。"""

import json

import pytest

from agent4pharma.composition import discovery_run_view
from agent4pharma.infrastructure.persistence.sqlite.application_storage import SqliteApplicationStorage
from agent4pharma.infrastructure.persistence.sqlite.archive_batch_repository import ArchiveBatchRepository
from agent4pharma.infrastructure.persistence.sqlite.coarse_screening import assess_coarse
from agent4pharma.modules.materials.collection_records import CollectedRecord
from agent4pharma.runtime.settings import ApplicationSettings


def record(store, version, title="Protein design with AI"):
    return store.materials.ingest_record(
        CollectedRecord(
            source="arxiv",
            external_id="123",
            title=title,
            abstract="Actual archived abstract",
            version=version,
            url="https://arxiv.org/abs/123",
        ),
        [],
    )


def test_consolidate_latest_version_and_immutable_history(tmp_path):
    root, archive = tmp_path / "main", tmp_path / "archive"
    with_store = SqliteApplicationStorage(archive)
    with with_store.db:
        with_store.db.execute("INSERT INTO sources(id,descriptor) VALUES ('arxiv','{}')")
        old = record(with_store, "v1")
        latest = record(with_store, "v2")
    with_store.close()
    target = SqliteApplicationStorage(root)
    config = ApplicationSettings(data_dir=root)
    target.create_run("2026-10-29", config)
    before = target.db.execute("SELECT * FROM discovery_batches").fetchall()
    repository = ArchiveBatchRepository(root)
    result = repository.consolidate("combined", [archive], config)
    assert (result.records, result.materials, result.sources) == (1, 1, 1)
    assert (
        target.db.execute("SELECT source_record_id FROM discovery_batch_items").fetchone()[0]
        == latest.record_id
    )
    assert target.db.execute("SELECT count(*) FROM source_records").fetchone()[0] == 2
    assert old.record_id != latest.record_id
    assert repository.consolidate("combined", [archive], config) == result
    assert target.db.execute("SELECT * FROM discovery_batches WHERE id!= 'combined'").fetchall() == before
    assert discovery_run_view(root, "combined").articles[0].category == "preprint"
    assert target.db.execute("PRAGMA foreign_key_check").fetchall() == []
    target.close()


def test_corrupt_artifact_rolls_back_import(tmp_path):
    archive, root = tmp_path / "archive", tmp_path / "main"
    origin = SqliteApplicationStorage(archive)
    with origin.db:
        origin.db.execute("INSERT INTO sources(id,descriptor) VALUES ('arxiv','{}')")
        record(origin, "v1")
    path = archive / origin.db.execute("SELECT storage_uri FROM artifacts LIMIT 1").fetchone()[0]
    path.write_text("corrupted", encoding="utf-8")
    origin.close()
    target = SqliteApplicationStorage(root)
    with pytest.raises(ValueError, match="哈希"):
        ArchiveBatchRepository(root).consolidate("broken", [archive], ApplicationSettings(data_dir=root))
    assert target.db.execute("SELECT count(*) FROM source_records").fetchone()[0] == 0
    assert target.db.execute("SELECT count(*) FROM discovery_batches").fetchone()[0] == 0
    target.close()


def test_legacy_demo_snapshot_is_readable(tmp_path):
    store = SqliteApplicationStorage(tmp_path)
    with store.db:
        store.db.execute(
            "INSERT INTO sources(id,descriptor,name,source_kind) "
            "VALUES ('demo-news','{}','Demo news','news_site')"
        )
        store.db.execute(
            "INSERT INTO materials(id,identity,title,identifiers,first_seen_at) "
            "VALUES ('m','demo:url:x','Demo','{}','2026-10-29')"
        )
        store.db.execute(
            "INSERT INTO source_records(id,source_id,material_id,external_id,version_key,"
            "metadata,observed_at) VALUES ('r','demo-news','m','x','v1',?,'2026-10-29')",
            (
                json.dumps(
                    {"dataset": "demo", "title": "LLM for drug discovery", "material_type": "news_report"}
                ),
            ),
        )
    snapshot = store.materials.source_record_snapshot("r")
    assert store.materials.source_record_snapshots(["r", "r"]) == {"r": snapshot}
    assert store.materials.source_record_snapshots([]) == {}
    with pytest.raises(KeyError, match="来源记录不存在"):
        store.materials.source_record_snapshots(["r", "missing"])
    assert snapshot.source_id == "demo-news"
    assert snapshot.metadata["material_type"] == "news_report"
    config = ApplicationSettings(data_dir=tmp_path)
    ArchiveBatchRepository(tmp_path).consolidate("demo-combined", [], config)
    assess_coarse(store.db, "demo-combined", config)
    article = discovery_run_view(tmp_path, "demo-combined").articles[0]
    assert article.category == "news"
    assert article.route == "analyze"
    assert article.state == "awaiting_intelligence"
    store.close()


def test_append_archives_does_not_replace_current_article(tmp_path):
    root, archive = tmp_path / "main", tmp_path / "archive"
    target = SqliteApplicationStorage(root)
    with target.db:
        current = record(target, "v2")
    config = ApplicationSettings(data_dir=root)
    repository = ArchiveBatchRepository(root)
    repository.consolidate("active", [], config)
    origin = SqliteApplicationStorage(archive)
    with origin.db:
        record(origin, "v3")
        origin.materials.ingest_record(CollectedRecord(source="arxiv", external_id="new", title="New"), [])
    origin.close()
    assert repository.append_archives("active", [archive]) == 1
    assert repository.append_archives("active", [archive]) == 0
    records = [r[0] for r in target.db.execute("SELECT source_record_id FROM discovery_batch_items")]
    assert current.record_id in records
    assert len(records) == 2
    target.close()
