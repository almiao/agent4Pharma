"""文章阅读投影只读取本批版本，区分全文、片段、受限链接与缺失文件。"""

from types import SimpleNamespace

import pytest

from agent4pharma.composition import discovery_article_reading, discovery_run_view
from agent4pharma.entrypoints.web.api import WorkbenchAPI
from agent4pharma.infrastructure.persistence.sqlite.application_storage import SqliteApplicationStorage
from agent4pharma.modules.materials.collection_records import CollectedContent, CollectedRecord
from agent4pharma.runtime.settings import ApplicationSettings


def archive(store, config, batch, version="v1", content=None, abstract="", metadata=None):
    with store.db:
        saved = store.materials.ingest_record(
            CollectedRecord(
                source="news",
                external_id="article",
                title="Actual news title",
                version=version,
                url="https://news.example/article",
                abstract=abstract,
                content=content,
                metadata={"material_type": "news_report", **(metadata or {})},
            ),
            [],
        )
        store.discovery.register_record(batch, saved, config.snapshot())
    return saved


@pytest.fixture
def reading_store(tmp_path):
    store = SqliteApplicationStorage(tmp_path)
    config = ApplicationSettings(data_dir=tmp_path)
    _, batch = store.create_run("2026-10-31", config)
    yield store, config, batch
    store.close()


def test_saved_body_paginates_and_api_preserves_text(reading_store):
    store, config, batch = reading_store
    text = "真实正文\n<script>untrusted text</script>\n" * 1500
    record = archive(store, config, batch, content=CollectedContent(text, "fulltext", "test-v1"))
    api = WorkbenchAPI(SimpleNamespace(cases=SimpleNamespace(root=store.root)))
    first = api.get(f"/api/discovery-run/article?batch={batch}&record={record.record_id}")
    assert first["availability"] == "fulltext"
    assert first["body"] == text[:20000]
    assert first["next_offset"] == 20000
    tail = discovery_article_reading(store.root, batch, record.record_id, 20000)
    assert tail.body == text[20000:40000]
    assert tail.url == "https://news.example/article"
    listing = discovery_run_view(store.root, batch).articles[0].model_dump()
    assert listing["url"] == tail.url
    assert "body" not in listing


def test_preview_and_abstract_do_not_claim_fulltext(reading_store):
    store, config, batch = reading_store
    preview = archive(store, config, batch, content=CollectedContent("Only a teaser", "preview", "test-v1"))
    assert discovery_article_reading(store.root, batch, preview.record_id).availability == "partial"
    summary = archive(store, config, batch, version="v2", abstract="Actual abstract")
    reading = discovery_article_reading(store.root, batch, summary.record_id)
    assert reading.availability == "abstract_only"
    assert reading.body == ""
    assert reading.abstract == "Actual abstract"


def test_restricted_link_and_batch_membership(reading_store):
    store, config, batch = reading_store
    record = archive(
        store, config, batch, metadata={"access_state": "http_403", "classification_state": "provisional"}
    )
    reading = discovery_article_reading(store.root, batch, record.record_id)
    assert reading.availability == "restricted"
    assert reading.url
    assert reading.title_is_provisional
    assert not reading.body
    with pytest.raises(KeyError, match="不属于"):
        discovery_article_reading(store.root, "unrelated", record.record_id)
    with pytest.raises(ValueError):
        discovery_article_reading(store.root, batch, record.record_id, -1)


def test_missing_archive_is_explicit(reading_store):
    store, config, batch = reading_store
    record = archive(store, config, batch, content=CollectedContent("Archived body", "fulltext", "test-v1"))
    row = store.db.execute("SELECT storage_uri FROM artifacts WHERE kind='fulltext'").fetchone()
    (store.root / row[0]).unlink()
    reading = discovery_article_reading(store.root, batch, record.record_id)
    assert reading.availability == "unavailable"
    assert reading.read_error == "已归档正文文件缺失"


def test_directory_category_overrides_stale_media_plan(reading_store, monkeypatch):
    store, config, batch = reading_store
    with store.db:
        store.db.execute(
            "INSERT INTO sources(id,descriptor,name,source_kind) "
            "VALUES ('news','{}','Journal','journal_site')"
        )
        record = store.materials.ingest_record(
            CollectedRecord(source="news", external_id="paper", title="Paper"), []
        )
        store.discovery.register_record(batch, record, config.snapshot())
    store.source_job(batch, "news", {"source": "news"})
    stale = SimpleNamespace(
        source_id="news",
        source_kind="news_site",
        material_type="news_report",
        homepage_url="https://news.example/",
        name="Old label",
        entries=[SimpleNamespace(url="https://news.example/feed")],
    )
    monkeypatch.setattr(
        "agent4pharma.composition.build_collection_plan_catalog",
        lambda root: SimpleNamespace(list_plans=lambda: [stale]),
    )
    view = discovery_run_view(store.root, batch)
    assert view.articles[0].category == "journal"
    assert view.sources[0].name == "Journal"
