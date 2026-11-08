"""失败复核不充当成功取得量，重试失败不因保存了诊断变成部分成功。"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agent4pharma.infrastructure.connectors.rss_feed import RssFeedConnector
from agent4pharma.infrastructure.persistence.sqlite.application_storage import SqliteApplicationStorage
from agent4pharma.infrastructure.persistence.sqlite.collection_diagnostics_repository import (
    CollectionDiagnosticsRepository,
)
from agent4pharma.infrastructure.persistence.sqlite.discovery_run_view import DiscoveryRunReader
from agent4pharma.modules.collection.acquisition_interfaces import CollectionDiagnostic
from agent4pharma.modules.collection.errors import SourceError
from agent4pharma.modules.collection.source_collection import CollectionService
from agent4pharma.runtime.settings import ApplicationSettings


def test_failed_diagnostic_is_visible_but_not_collection_progress(tmp_path):
    store = SqliteApplicationStorage(tmp_path)
    config = ApplicationSettings(
        data_dir=tmp_path, sources={"test": {"feed_url": "https://example.com/feed"}}
    )
    run, batch = store.create_run("2026-10-31", config)
    job = store.source_job(
        run,
        "test",
        {"source": "test", "start": "2026-10-30T00:00:00+00:00", "end": "2026-10-31T00:00:00+00:00"},
    )
    store.set_job(job["id"], "failed", error="Original safe failure", attempt=True)
    diagnostic = CollectionDiagnostic(
        source_id="test",
        url="https://example.com/feed",
        checked_at=datetime.now(timezone.utc),
        http_status=403,
        reason="上游拒绝请求",
    )
    repository = CollectionDiagnosticsRepository(tmp_path)
    repository.save(batch, [diagnostic])
    source = DiscoveryRunReader(tmp_path).snapshot(batch).sources[0]
    assert source.error == "Original safe failure"
    assert source.records is None
    assert source.diagnostic.http_status == 403
    assert source.request_url_basis == "frozen_plan"
    with pytest.raises(ValueError):
        repository.save(batch, [diagnostic.model_copy(update={"source_id": "other"})])

    def unavailable(*args):
        raise SourceError("Still unavailable")

    CollectionService(config, store, {"test": SimpleNamespace(page=unavailable)}).collect_source(
        run,
        batch,
        "test",
        config.sources["test"],
        datetime(2026, 10, 30, tzinfo=timezone.utc),
        datetime(2026, 10, 31, tzinfo=timezone.utc),
    )
    assert store.job(job["id"])["status"] == "failed"
    assert store.job(job["id"])["attempt"] == 2
    store.close()


def test_rss_http_failure_keeps_status_code_without_response_body():
    connector = RssFeedConnector("test", "https://example.com/feed")
    connector.fetcher = SimpleNamespace(fetch=lambda url: SimpleNamespace(http_status=406))
    with pytest.raises(SourceError, match="HTTP 406"):
        connector.page(datetime.now(timezone.utc), datetime.now(timezone.utc), "", None)
