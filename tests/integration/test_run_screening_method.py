"""运行展示必须区分直接精筛与真正的粗筛判断，不改写历史路由。"""

from agent4pharma.infrastructure.persistence.sqlite.application_storage import SqliteApplicationStorage
from agent4pharma.infrastructure.persistence.sqlite.coarse_screening import assess_coarse
from agent4pharma.infrastructure.persistence.sqlite.discovery_run_view import DiscoveryRunReader
from agent4pharma.modules.materials.collection_records import CollectedRecord
from agent4pharma.runtime.settings import ApplicationSettings


def test_semantic_passthrough_is_not_reported_as_model_screening(tmp_path):
    store = SqliteApplicationStorage(tmp_path)
    config = ApplicationSettings(data_dir=tmp_path, sources={"journal": {"coarse_mode": "semantic_only"}})
    _, batch = store.create_run("2026-10-30", config)
    with store.db:
        record = store.materials.ingest_record(
            CollectedRecord(source="journal", external_id="paper", title="Unrelated title"), []
        )
        store.discovery.register_record(batch, record, config.snapshot())
    assess_coarse(store.db, batch, config)
    article = DiscoveryRunReader(tmp_path).snapshot(batch).articles[0]
    assert article.route == "analyze"
    assert article.state == "awaiting_intelligence"
    assert article.triage_method == "semantic_passthrough"
    assert not article.triage_id
    store.close()


def test_keyword_miss_no_longer_excludes_collected_record(tmp_path):
    """关键词没有命中也不能丢资料；未启用本地判别时全部进入情报队列。"""
    store = SqliteApplicationStorage(tmp_path)
    config = ApplicationSettings(data_dir=tmp_path, sources={"pubmed": {"query": ""}})
    _, batch = store.create_run("2026-10-30", config)
    with store.db:
        record = store.materials.ingest_record(
            CollectedRecord(source="pubmed", external_id="paper", title="Black holes"), []
        )
        store.discovery.register_record(batch, record, config.snapshot())
    assess_coarse(store.db, batch, config)
    article = DiscoveryRunReader(tmp_path).snapshot(batch).articles[0]
    assert article.state == "awaiting_intelligence"
    assert article.route == "analyze"
    assert article.triage_method == "semantic_passthrough"
    store.close()
