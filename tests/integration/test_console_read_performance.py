"""只读性能优化的契约回归，不以机器相关耗时判断正确性。"""

import gzip
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from agent4pharma.composition import build_maintenance_workbench
from agent4pharma.entrypoints.web.server import WorkbenchHTTPServer
from agent4pharma.infrastructure.persistence.sqlite.article_regression_repository import ObservationRepository
from agent4pharma.infrastructure.persistence.sqlite.discovery_query_cache import DiscoveryQueryCache
from agent4pharma.infrastructure.persistence.sqlite.production_unit import production_unit
from agent4pharma.modules.discovery.run_records import DiscoveryRunSnapshot
from agent4pharma.runtime.settings import ApplicationSettings
from agent4pharma.runtime.workflow_observation import WorkflowObservation


@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
def test_discovery_cache_invalidates_on_commits_and_returns_independent_objects(tmp_path, journal_mode):
    path = tmp_path / "discovery.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA journal_mode={journal_mode}")
        db.execute("CREATE TABLE cache_probe (value INTEGER)")
        db.execute("INSERT INTO cache_probe(value) VALUES (1)")
    reads = []

    def read(batch):
        reads.append(batch)
        with sqlite3.connect(path) as db:
            value = db.execute("SELECT value FROM cache_probe").fetchone()[0]
        return DiscoveryRunSnapshot(selected=str(value))

    cache = DiscoveryQueryCache(tmp_path, read)
    try:
        cache.get("b")["selected"] = "corrupted caller value"
        assert cache.get("b")["selected"] == "1"
        assert reads == ["b"]
        with sqlite3.connect(path) as db:
            db.execute("UPDATE cache_probe SET value=2")
        assert cache.get("b")["selected"] == "2"
        assert reads == ["b", "b"]
        for index in range(10):
            cache.get(str(index))
        assert len(cache.values) == 8
    finally:
        cache.close()
    assert cache.connection is None


def test_read_snapshot_does_not_acquire_writer_lock_and_cannot_mutate(tmp_path):
    app = build_maintenance_workbench(tmp_path / "config.toml", ApplicationSettings(data_dir=tmp_path))
    try:

        def read():
            with production_unit(tmp_path, read_only=True) as work:
                assert work.tasks.list() == []
                with pytest.raises(sqlite3.OperationalError, match="readonly"):
                    work.tasks.connection.execute("DELETE FROM production_tasks")

        with production_unit(tmp_path):
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(read).result(timeout=2)
    finally:
        app.close()


def test_observation_operation_filter_runs_before_limit_and_keeps_subject_scope(tmp_path):
    app = build_maintenance_workbench(tmp_path / "config.toml", ApplicationSettings(data_dir=tmp_path))
    try:
        sink = ObservationRepository(tmp_path)
        for index in range(1002):
            sink.append(
                WorkflowObservation(
                    id=f"event-{index}",
                    subject_ref="task",
                    stage="content",
                    operation="tool",
                    status="succeeded",
                    created_at="2026-11-05T00:00:00Z",
                )
            )
        for subject in ("task", "other"):
            sink.append(
                WorkflowObservation(
                    id=subject,
                    subject_ref=subject,
                    stage="content",
                    operation="token_usage",
                    status="succeeded",
                    created_at="2026-11-05T00:00:00Z",
                )
            )
        assert [e.id for e in sink.list(["task"], operations=("token_usage",))] == ["task"]
        assert len(sink.list(["task"])) == 1000
        assert sink.list([], operations=("token_usage",)) == []
    finally:
        app.close()


def test_hashed_assets_cache_but_html_and_api_remain_private_and_gzip_negotiated(tmp_path):
    app = build_maintenance_workbench(tmp_path / "config.toml", ApplicationSettings(data_dir=tmp_path))
    server = WorkbenchHTTPServer(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
            html = client.get("/console")
            assert html.headers["cache-control"] == "no-store"
            asset = re.search(r'src="(/console-assets/assets/[^\"]+\.js)"', html.text)[1]
            plain = client.get(asset, headers={"Accept-Encoding": "identity"})
            assert plain.headers["cache-control"].endswith("immutable")
            assert "content-encoding" not in plain.headers
            with client.stream("GET", asset, headers={"Accept-Encoding": "gzip"}) as response:
                assert response.headers["content-encoding"] == "gzip"
                assert response.headers["vary"] == "Accept-Encoding"
                assert gzip.decompress(b"".join(response.iter_raw())) == plain.content
            data = client.get("/api/production")
            assert data.status_code == 403
            assert data.headers["cache-control"] == "no-store"
            assert "unsafe-inline" not in html.headers["content-security-policy"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        app.close()
