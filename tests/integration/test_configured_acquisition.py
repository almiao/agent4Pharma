import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent4pharma.composition import (
    build_collection_plan_catalog,
    build_daily_literature_discovery,
    open_application_storage,
    save_collection_plan,
)
from agent4pharma.infrastructure.connectors import configured_web
from agent4pharma.infrastructure.web import public_fetch
from agent4pharma.infrastructure.web.configured_extraction import timestamp, validate_selectors
from agent4pharma.modules.maintenance.repository import FetchedDocument
from agent4pharma.modules.sources.collection_plans import CollectionPlan
from agent4pharma.runtime.settings import ApplicationSettings


def plan_definition():
    return {
        "source_id": "example-media",
        "name": "Example Media",
        "homepage_url": "https://news.example/",
        "allowed_hosts": ["news.example"],
        "entries": [
            {"url": "https://news.example/feed", "kind": "rss"},
            {
                "url": "https://news.example/list",
                "kind": "html",
                "link_selector": "h2 a",
                "next_selector": 'a[rel="next"]',
                "max_pages": 2,
            },
        ],
        "article": {
            "body": {"selectors": ["article"]},
            "external_id": {"selectors": ["article[data-id]"], "attribute": "data-id"},
            "remove_selectors": ["nav", "footer"],
            "paywall_selectors": [".paywall"],
        },
    }


def article(identity, text="LLM agents discover medicines. ", extra=""):
    return (
        f'<link rel="canonical" href="/story/{identity}"><h1>Article {identity}</h1>'
        '<meta property="article:published_time" content="2026-10-01T12:00:00+00:00">'
        f'<article data-id="{identity}"><nav>Navigation noise</nav>{text * 20}{extra}</article>'
    )


class FakeWeb:
    def __init__(self):
        self.requests = []
        self.responses = {
            "/feed": "<rss><channel><item><title>A</title><link>https://news.example/alias/a</link>"
            "</item></channel></rss>",
            "/list": '<h2><a href="/story/a?utm_source=test">A</a></h2><a rel="next" href="/list/2">Next</a>',
            "/list/2": '<h2><a href="/story/b">B</a></h2>',
            "/alias/a": article("a"),
            "/story/a": article("a"),
            "/story/b": article("b", extra='<div class="paywall">Subscribers only</div>'),
        }
        self.fail = set()

    def fetch(self, url):
        from urllib.parse import urlsplit

        path = urlsplit(url).path
        self.requests.append(path)
        status = 403 if path in self.fail else 200
        body = "Denied" if status == 403 else self.responses[path]
        return FetchedDocument(
            url, body.encode(), "application/xml" if path == "/feed" else "text/html", status
        )


@pytest.fixture
def system(tmp_path, monkeypatch):
    store = open_application_storage(tmp_path)
    web = FakeWeb()
    monkeypatch.setattr(public_fetch, "PublicPageFetcher", lambda **kwargs: web)
    monkeypatch.setattr(configured_web.time, "sleep", lambda seconds: None)
    plan = CollectionPlan.model_validate(plan_definition())
    catalog = build_collection_plan_catalog(tmp_path)
    catalog.save(plan)
    config = ApplicationSettings(data_dir=tmp_path, sources={})
    workflow = build_daily_literature_discovery(config, store, configured_only=True)
    yield store, web, workflow, catalog
    workflow.close()
    store.close()


def test_multi_entry_aliases_fulltext_preview_and_idempotent_versions(system):
    store, web, workflow, catalog = system
    report = workflow.run(date(2026, 10, 28))
    assert report["coverage_status"] == "complete"
    assert report["unique_materials"] == 2
    assert "/list/2" in web.requests
    assert store.db.execute("SELECT count(*) FROM source_records").fetchone()[0] == 2
    kinds = {r[0] for r in store.db.execute("SELECT kind FROM artifacts")}
    assert {"fulltext", "preview", "raw_response"} <= kinds
    bodies = [
        json.loads(r[0])["content"]["text"] for r in store.db.execute("SELECT metadata FROM source_records")
    ]
    assert all("Navigation noise" not in text for text in bodies)
    workflow.run(date(2026, 10, 29))
    assert store.db.execute("SELECT count(*) FROM source_records").fetchone()[0] == 2
    assert (
        store.db.execute("SELECT canonical_url FROM materials ORDER BY identity").fetchone()[0].endswith("/a")
    )
    assert report["sources"][0]["result"]["date_basis"].startswith("configured_current_window")
    # 10 月 1 日发表的资料仍可在本轮首次发现，不误套昨日发表过滤。
    assert (
        store.db.execute("SELECT published_at FROM materials LIMIT 1").fetchone()[0].startswith("2026-10-01")
    )


def test_failed_detail_preserves_success_and_resumes_original_queue(system):
    store, web, workflow, catalog = system
    web.fail.add("/story/b")
    first = workflow.run(date(2026, 10, 28))
    assert first["status"] == "partial"
    assert first["unique_materials"] == 1
    assert "403" in first["sources"][0]["error"]
    before = list(web.requests)
    web.fail.clear()
    second = workflow.run(date(2026, 10, 28))
    assert second["coverage_status"] == "complete"
    assert web.requests[len(before) :] == ["/story/b"]
    assert store.db.execute("SELECT count(*) FROM source_records").fetchone()[0] == 2


def test_content_update_keeps_material_identity_and_adds_version(system):
    store, web, workflow, catalog = system
    workflow.run(date(2026, 10, 28))
    web.responses["/alias/a"] = web.responses["/story/a"] = article("a", "Revised LLM drug research. ")
    workflow.run(date(2026, 10, 29))
    assert store.db.execute("SELECT count(*) FROM materials").fetchone()[0] == 2
    assert store.db.execute("SELECT count(*) FROM source_records").fetchone()[0] == 3
    assert store.db.execute("SELECT count(*) FROM source_collection_plan_versions").fetchone()[0] == 1


def test_plan_import_preserves_demo_source_and_records_versions(system, tmp_path):
    store, web, workflow, catalog = system
    with store.db:
        store.db.execute(
            "INSERT INTO sources(id,descriptor,name,homepage_url) VALUES (?,?,?,?)",
            ("demo-existing", '{"dataset":"demo"}', "Existing", "https://other.example/"),
        )
    definition = plan_definition()
    definition.update(source_id="new-name", homepage_url="https://other.example/")
    new = catalog.save(CollectionPlan.model_validate(definition))
    assert new.source_id == "demo-existing"
    catalog.save(CollectionPlan.model_validate(definition))
    assert (
        store.db.execute(
            "SELECT count(*) FROM source_collection_plan_versions WHERE source_id='demo-existing'"
        ).fetchone()[0]
        == 1
    )
    catalog.save(new.model_copy(update={"enabled": False}), expected_hash=new.fingerprint())
    assert "demo-existing" not in {p.source_id for p in catalog.list_plans(enabled_only=True)}


def test_bad_selector_and_executable_configuration_rejected():
    definition = plan_definition()
    definition["article"]["body"]["selectors"] = ["article["]
    with pytest.raises(ValueError, match="CSS"):
        validate_selectors(CollectionPlan.model_validate(definition))
    definition["article"]["python"] = "print('must not run')"
    with pytest.raises(ValidationError):
        CollectionPlan.model_validate(definition)


def test_date_precision_and_missing_body_are_not_fulltext(system):
    store, web, workflow, catalog = system
    assert timestamp("2026-10-26") is None
    assert timestamp("2026-10-26T10:00:00") is None
    web.responses["/alias/a"] = web.responses["/story/a"] = "<h1>Access denied</h1><main>Login</main>"
    report = workflow.run(date(2026, 10, 28))
    assert report["coverage_status"] == "partial"
    assert store.db.execute("SELECT count(*) FROM materials").fetchone()[0] == 1
    assert "身份" in report["sources"][0]["error"]


def test_body_reference_is_preserved_without_merging_news_into_paper(system):
    store, web, workflow, catalog = system
    body = article("a", extra='<a href="https://doi.org/10.1234/example">Research paper</a>')
    web.responses["/alias/a"] = web.responses["/story/a"] = body
    result = workflow.run(date(2026, 10, 28))
    assert result["unique_materials"] == 2
    row = store.db.execute("SELECT identity,id FROM materials WHERE identity='example-media:a'").fetchone()
    assert row is not None
    record = store.material(row["id"])["records"][0]["metadata"]
    assert record["content"]["links"] == [
        {"url": "https://doi.org/10.1234/example", "label": "Research paper"}
    ]


def test_config_snapshot_and_repository_do_not_depend_on_seed_file(system, tmp_path):
    store, web, workflow, catalog = system
    snapshot = workflow.config.sources["example-media"]["plan_json"]
    plan = catalog.list_plans()[0]
    catalog.save(
        plan.model_copy(update={"article": plan.article.model_copy(update={"minimum_body_chars": 9999})}),
        expected_hash=plan.fingerprint(),
    )
    assert workflow.config.sources["example-media"]["plan_json"] == snapshot
    assert workflow.run(date(2026, 10, 28))["unique_materials"] == 2


@pytest.mark.parametrize("legacy_prompt", [False, True])
def test_resume_reconstructs_old_configuration_after_catalog_edit(system, legacy_prompt):
    store, web, workflow, catalog = system
    if legacy_prompt:
        workflow.config.freeze_snapshot(
            {**workflow.config.snapshot(), "briefing_prompt": "Historical prompt"}
        )
    web.fail.add("/story/b")
    first = workflow.run(date(2026, 10, 28))
    plan = catalog.list_plans()[0]
    changed_rules = plan.article.model_copy(update={"minimum_body_chars": 9999})
    catalog.save(plan.model_copy(update={"article": changed_rules}), expected_hash=plan.fingerprint())
    web.fail.clear()
    resumed = build_daily_literature_discovery(workflow.config, store, frozen_batch=first["batch_id"])
    result = resumed.run(date(2026, 10, 28))
    assert result["batch_id"] == first["batch_id"]
    assert result["unique_materials"] == 2
    assert result["coverage_status"] == "complete"


def test_page_transaction_failure_does_not_advance_record_or_cursor(system):
    store, web, workflow, catalog = system
    with store.db:
        store.db.execute(
            "CREATE TRIGGER reject_batch BEFORE INSERT ON discovery_batch_items "
            "BEGIN SELECT RAISE(ABORT,'forced rollback'); END"
        )
    report = workflow.run(date(2026, 10, 28))
    assert report["coverage_status"] == "partial"
    assert store.db.execute("SELECT count(*) FROM source_records").fetchone()[0] == 0
    assert report["sources"][0]["result"]["cursor"]["index"] == 0
    with store.db:
        store.db.execute("DROP TRIGGER reject_batch")
    assert workflow.run(date(2026, 10, 28))["unique_materials"] == 2


def test_v6_backup_restores_unchanged_v5_database(tmp_path):
    from agent4pharma.infrastructure.persistence.sqlite.migrations.v001 import SCHEMA
    from agent4pharma.infrastructure.persistence.sqlite.migrations.v002 import MIGRATION_2
    from agent4pharma.infrastructure.persistence.sqlite.migrations.v003 import MIGRATION_3
    from agent4pharma.infrastructure.persistence.sqlite.migrations.v004 import MIGRATION_4
    from agent4pharma.infrastructure.persistence.sqlite.migrations.v005 import MIGRATION_5

    path = tmp_path / "discovery.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO schema_version(version) VALUES (1)")
        db.commit()
        for migration in [MIGRATION_2, MIGRATION_3, MIGRATION_4, MIGRATION_5]:
            db.executescript(migration)
        db.execute(
            "INSERT INTO materials(id,identity,title,identifiers,first_seen_at) VALUES (?,?,?,?,?)",
            ("old", "old", "Keep", "{}", "2026-10-29"),
        )
    store = open_application_storage(tmp_path)
    store.close()
    backup = next((tmp_path / "backups").glob("discovery-pre-v6-*.sqlite3"))
    restored = tmp_path / "restore.sqlite3"
    with sqlite3.connect(backup) as source, sqlite3.connect(restored) as target:
        source.backup(target)
        assert target.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 5
        assert target.execute("SELECT title FROM materials WHERE id='old'").fetchone()[0] == "Keep"


def test_engine_contains_no_site_specific_configuration():
    source = Path(configured_web.__file__).read_text(encoding="utf-8").lower()
    assert all(
        name not in source for name in ["biopharmatrend", "decodingbio", "biotender", "endpoints.news"]
    )


def test_database_plan_update_rejects_missing_or_stale_version(system):
    store, web, workflow, catalog = system
    original = catalog.get("example-media")
    changed = original.model_copy(update={"name": "Changed name"})
    for expected in (None, "stale"):
        with pytest.raises(ValueError, match="版本"):
            save_collection_plan(changed.model_dump_json(), store.root, expected_hash=expected)
        assert catalog.get(original.source_id).name == original.name
    saved = save_collection_plan(changed.model_dump_json(), store.root, expected_hash=original.fingerprint())
    assert catalog.get(saved.source_id).name == "Changed name"
    with pytest.raises(ValueError, match="版本"):
        catalog.save(original, expected_hash=original.fingerprint())
    assert store.db.execute("SELECT count(*) FROM source_collection_plan_versions").fetchone()[0] == 2


def test_database_plan_management_cli_reads_saves_and_toggles(system, monkeypatch, capsys):
    import io
    import sys

    from agent4pharma.entrypoints.cli.acquisition_commands import acquisition_command
    from agent4pharma.entrypoints.cli.argument_parser import build_parser

    store, web, workflow, catalog = system
    parser = build_parser()

    def command(*arguments):
        args = parser.parse_args(["acquisition", *arguments])
        acquisition_command(args, workflow.config, store)
        return json.loads(capsys.readouterr().out)

    current = command("get", "example-media")
    current["plan"]["name"] = "Saved directly to database"
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(current["plan"])))
    saved = command("save", "--expected-hash", current["content_hash"])
    assert saved["plan"]["name"] == "Saved directly to database"
    paused = command("disable", "example-media", "--expected-hash", saved["content_hash"])
    assert not paused["plan"]["enabled"]
    assert catalog.list_plans(enabled_only=True) == []
    active = command("enable", "example-media", "--expected-hash", paused["content_hash"])
    assert active["plan"]["enabled"]
    assert catalog.list_plans(enabled_only=True)[0].name == "Saved directly to database"


def test_rdf_dates_newest_first_before_limit_and_legacy_identity():
    from datetime import datetime, timezone

    from agent4pharma.infrastructure.connectors.rss_feed import parse_feed
    from agent4pharma.modules.collection.errors import SourceError

    raw = b"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
        <item><title>Old</title><link>https://news.example/story/a</link>
          <dc:date>2026-10-22T11:00:46-07:00</dc:date></item>
        <item><title>Latest AI clinical framework</title><link>https://news.example/story/b</link>
          <dc:date>2026-10-28T07:29:01-07:00</dc:date></item></rdf:RDF>"""
    records = parse_feed(raw, "https://news.example/feed", "test")
    assert records[1].published == "2026-10-28T14:29:01+00:00"
    assert records[1].metadata["date_field"] == "date"
    definition = plan_definition()
    definition["entries"] = [{"url": "https://news.example/feed", "kind": "rss", "max_links": 1}]
    original = CollectionPlan.model_validate(definition)
    assert "order" not in original.model_dump()["entries"][0]
    assert (
        CollectionPlan.model_validate_json(original.model_dump_json()).fingerprint() == original.fingerprint()
    )
    definition["entries"][0]["order"] = "newest_first"
    plan = CollectionPlan.model_validate(definition)
    assert plan.fingerprint() != original.fingerprint()
    web = FakeWeb()
    web.responses["/feed"] = raw.decode()
    connector = configured_web.ConfiguredWebConnector(plan, web)
    now = datetime.now(timezone.utc)
    discovery = connector.page(now, now, "", None)
    assert len(discovery.next_cursor["candidates"]) == 1
    assert discovery.next_cursor["discovery_windows"][0]["available"] == 2
    assert discovery.next_cursor["discovery_windows"][0]["selected"] == 1
    assert discovery.next_cursor["candidates"][0]["title"] == "Latest AI clinical framework"
    assert discovery.next_cursor["candidates"][0]["url"] == "https://news.example/story/b"
    web.responses["/feed"] = raw.decode().replace("2026-10-28T07:29:01-07:00", "2026-10-28")
    with pytest.raises(SourceError, match="未知"):
        connector.page(now, now, "", None)
