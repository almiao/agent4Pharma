import json
import sqlite3

import pytest

from agent4pharma.infrastructure.persistence.sqlite.application_storage import SqliteApplicationStorage
from agent4pharma.infrastructure.persistence.sqlite.source_directory_repository import import_demo
from agent4pharma.modules.sources.directory_reports import write_demo_review
from agent4pharma.runtime.settings import ApplicationSettings


@pytest.fixture
def demo_seed(tmp_path):
    materials = []
    for name, source, kind in [
        ("article", "media", "editorial_article"),
        ("blocked", "media", "news_report"),
        ("conference", "conference", "web_page"),
    ]:
        materials.append(
            {
                "source_id": "demo-" + source,
                "organization_id": "demo-publisher",
                "url": "https://example.org/" + name,
                "title": name,
                "material_type": kind,
                "access_state": "linked_target_not_fetched",
                "references": [],
            }
        )
    materials[0]["references"] = [
        {
            "url": "https://example.org/research",
            "label": "Research",
            "relation": "cites",
            "role": "unassessed",
        }
    ]
    materials[1].update(http_status=403, access_state="http_403", reported_subject_names=["Example Company"])
    materials[2].update(venue_key="conference", edition_id="demo-edition")
    seed = {
        "version": "test-demo-v1",
        "observed_at": "2026-10-29",
        "organizations": [{"id": "demo-publisher", "name": "Example Media", "kind": "media"}],
        "venues": [{"key": "conference", "name": "Example Conference", "kind": "conference"}],
        "conference_editions": [
            {"id": "demo-edition", "venue_key": "conference", "year": 2026, "name": "Example Conference 2026"}
        ],
        "sources": [
            {
                "id": "demo-" + name,
                "name": "Example " + name,
                "source_kind": "website",
                "organization_id": "demo-publisher",
                "homepage_url": "https://example.org/" + name,
            }
            for name in ["media", "conference"]
        ],
        "endpoints": [
            {
                "id": "demo-endpoint-" + name,
                "source_id": "demo-" + name,
                "url": "https://example.org/" + name,
                "access_method": "html",
                "access_state": "candidate_not_tested",
            }
            for name in ["media", "conference"]
        ],
        "materials": materials,
    }
    path = tmp_path / "demo.json"
    path.write_text(json.dumps(seed), encoding="utf-8")
    return path


def test_demo_is_repeatable_isolated_and_preserves_catalog(tmp_path, demo_seed):
    store = SqliteApplicationStorage(tmp_path)
    try:
        first = import_demo(store, demo_seed)
        times = store.db.execute("SELECT id,first_seen_at FROM materials ORDER BY id").fetchall()
        again = import_demo(store, demo_seed)
        assert first == again
        assert times == store.db.execute("SELECT id,first_seen_at FROM materials ORDER BY id").fetchall()
        assert first["material_count"] == 3
        assert first["source_count"] == 2
        review = write_demo_review(store, first).read_text(encoding="utf-8")
        assert "Example media" in review
        assert "http_403" in review
        assert "cites / unassessed" in review
        assert store.db.execute("SELECT sum(enabled) FROM source_endpoints").fetchone()[0] == 0
        assert store.db.execute("SELECT count(*) FROM discovery_batch_items").fetchone()[0] == 0
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert store.db.execute("SELECT count(*) FROM conference_editions").fetchone()[0] == 1
        subject = next(r for r in first["records"] if r["metadata"].get("http_status") == 403)
        assert subject["metadata"]["references"] == []
        assert subject["metadata"]["reported_subject_names"] == ["Example Company"]
        assert subject["metadata"]["organization_id"] != "Example Company"
    finally:
        store.close()


def test_failed_demo_import_rolls_back(tmp_path, demo_seed):
    seed = json.loads(demo_seed.read_text(encoding="utf-8"))
    seed["materials"][-1]["organization_id"] = "missing-organization"
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(seed), encoding="utf-8")
    store = SqliteApplicationStorage(tmp_path / "db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            import_demo(store, broken)
        assert store.db.execute("SELECT count(*) FROM organizations").fetchone()[0] == 0
        assert store.db.execute("SELECT count(*) FROM materials").fetchone()[0] == 0
    finally:
        store.close()


def test_source_seed_updates_without_replacing_referenced_source(tmp_path):
    store = SqliteApplicationStorage(tmp_path)
    try:
        config = ApplicationSettings(sources={"pubmed": {}})
        store.seed_sources(config)
        store.db.execute("UPDATE sources SET name='Keep metadata' WHERE id='pubmed'")
        store.db.execute(
            "INSERT INTO source_endpoints(id,source_id,url,access_method) "
            "VALUES ('endpoint','pubmed','https://example.org','api')"
        )
        store.db.commit()
        store.seed_sources(config)
        assert store.db.execute("SELECT name FROM sources WHERE id='pubmed'").fetchone()[0] == "Keep metadata"
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        store.close()
