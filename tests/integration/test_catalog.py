import json
import sqlite3

import pytest

from agent4pharma.infrastructure.persistence.sqlite.application_storage import SqliteApplicationStorage
from agent4pharma.infrastructure.persistence.sqlite.catalog_importer import import_catalog
from agent4pharma.infrastructure.persistence.sqlite.migrations.v001 import SCHEMA
from agent4pharma.infrastructure.persistence.sqlite.venue_repository import list_catalog
from agent4pharma.modules.sources.venue_identity import normalize_name


def files(tmp_path, venues, observed):
    seed = tmp_path / "seed.json"
    inventory = tmp_path / "inventory.json"
    seed.write_text(
        json.dumps({"version": "test-v1", "observed_at": "2026-10-29", "venues": venues}), encoding="utf-8"
    )
    inventory.write_text(
        json.dumps(
            {
                "source_url": "https://example.org/summary.json",
                "snapshot_generated_at": "2026-06-16",
                "record_count": 5,
                "missing_venue_count": 0,
                "venues": observed,
            }
        ),
        encoding="utf-8",
    )
    return seed, inventory


def entry(name, aliases=None, baseline=True):
    return {
        "name": name,
        "kind": "journal",
        "watch_status": "watch",
        "baseline_28": baseline,
        "aliases": aliases or [],
        "reference_url": "https://example.org/journal",
    }


def observed(name, count=1):
    return {"name": name, "variants": [name], "count": count, "topics": {}, "sources": {}, "examples": []}


def test_non_destructive_schema_migration(tmp_path):
    db = sqlite3.connect(tmp_path / "discovery.sqlite3")
    db.executescript(SCHEMA)
    db.execute("INSERT INTO schema_version VALUES (1)")
    db.execute("INSERT INTO materials VALUES ('m','doi:x','Existing','{}','2026-10-28')")
    db.commit()
    db.close()
    store = SqliteApplicationStorage(tmp_path)
    try:
        assert store.db.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 13
        assert store.db.execute("SELECT title FROM materials WHERE id='m'").fetchone()[0] == "Existing"
    finally:
        store.close()
    backups = list((tmp_path / "backups").glob("*pre-v4-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("SELECT title FROM materials WHERE id='m'").fetchone()[0] == "Existing"


def test_aliases_metrics_and_idempotent_import(tmp_path):
    nature = entry("Nature")
    nature["metric"] = {
        "value": 56.1,
        "metric_year": 2025,
        "source_url": "https://example.org/metrics",
        "verification_state": "official_value",
    }
    science = entry("Science", ["Science (New York, N.Y.)"], baseline=False)
    seed, inventory = files(
        tmp_path,
        [nature, science],
        [
            observed("Nature", 2),
            observed("Science", 1),
            observed("Science (New York, N.Y.)", 2),
            observed("Unknown Proceedings"),
        ],
    )
    store = SqliteApplicationStorage(tmp_path / "db")
    try:
        result = import_catalog(store, seed, inventory)
        count = store.db.execute("SELECT count(*) FROM venue_evidence").fetchone()[0]
        import_catalog(store, seed, inventory)
        assert store.db.execute("SELECT count(*) FROM venue_evidence").fetchone()[0] == count
        assert len(list_catalog(store)) == 3
        assert result["overlap_with_baseline"][0]["name"] == "Nature"
        addition = next(r for r in result["additions_to_baseline"] if r["name"] == "Science")
        assert addition["count"] == 3
        science_row = next(r for r in list_catalog(store) if r["name"] == "Science")
        assert science_row["metric"] is None
        unknown = next(r for r in list_catalog(store) if r["name"] == "Unknown Proceedings")
        assert unknown["kind"] == "unknown"
        assert unknown["watch_status"] == "unreviewed"
    finally:
        store.close()


def test_later_curated_alias_can_merge_only_raw_observation(tmp_path):
    seed, inventory = files(
        tmp_path, [entry("Nature Reviews Chemistry")], [observed("Nature reviews. Chemistry", 2)]
    )
    store = SqliteApplicationStorage(tmp_path / "db")
    try:
        import_catalog(store, seed, inventory)
        assert len(list_catalog(store)) == 2
        new_seed = json.loads(seed.read_text())
        new_seed["venues"][0]["aliases"] = ["Nature reviews. Chemistry"]
        seed.write_text(json.dumps(new_seed), encoding="utf-8")
        result = import_catalog(store, seed, inventory)
        assert len(list_catalog(store)) == 1
        assert result["overlap_with_baseline"][0]["count"] == 2
        assert (
            store.db.execute(
                "SELECT sum(record_count) FROM venue_evidence WHERE source_kind='biotender_discovery'"
            ).fetchone()[0]
            == 2
        )
    finally:
        store.close()


def test_missing_metric_year_is_not_silently_verified(tmp_path):
    value = entry("Journal")
    value["metric"] = {
        "value": 6.4,
        "metric_year": None,
        "source_url": "https://example.org/metrics",
        "verification_state": "official_value",
    }
    seed, inventory = files(tmp_path, [value], [])
    store = SqliteApplicationStorage(tmp_path / "db")
    try:
        with pytest.raises(ValueError, match="Missing metric year"):
            import_catalog(store, seed, inventory)
        assert list_catalog(store) == []
        value["metric"]["verification_state"] = "year_pending"
        seed, inventory = files(tmp_path, [value], [])
        import_catalog(store, seed, inventory)
        assert list_catalog(store)[0]["metric"]["metric_year"] is None
    finally:
        store.close()


def test_normalization_is_conservative():
    assert normalize_name("  Nature   Communications. ") == "nature communications"
    assert normalize_name("Computers &amp; Biology") == "computers & biology"
    assert normalize_name("Nature") != normalize_name("Nature Biotechnology")


def test_reviewed_journals_are_not_silently_merged(tmp_path):
    seed, inventory = files(tmp_path, [entry("Journal A"), entry("Journal B")], [])
    store = SqliteApplicationStorage(tmp_path / "db")
    try:
        import_catalog(store, seed, inventory)
        value = json.loads(seed.read_text())
        value["venues"][0]["aliases"] = ["Journal B"]
        seed.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(ValueError, match="reviewed/enriched"):
            import_catalog(store, seed, inventory)
        assert {r["name"] for r in list_catalog(store)} == {"Journal A", "Journal B"}
    finally:
        store.close()
