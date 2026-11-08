"""Two-role contracts and actual persistence/MCP; models and network remain offline."""

import sqlite3

import pytest

from agent4pharma.composition import (
    build_daily_literature_discovery,
    build_investigation_tools,
    build_maintenance_workbench,
)
from agent4pharma.modules.maintenance.change_contracts import MaintenanceChange
from agent4pharma.modules.maintenance.models import ArticleCaseInput, InvestigationRequest, InvestigationStart
from agent4pharma.modules.maintenance.repository import FetchedDocument
from agent4pharma.modules.sources.management import SourceEdit
from agent4pharma.runtime.settings import ApplicationSettings


@pytest.fixture
def system(tmp_path):
    config = ApplicationSettings(data_dir=tmp_path, sources={"biorxiv": {"query": ""}})
    app = build_maintenance_workbench(tmp_path / "config.toml", config)
    yield config, app
    app.close()


def running_investigation(config, app, text="LLM for drug discovery"):
    case = app.cases.save_case(ArticleCaseInput(reference="Manual pasted article", text=text))
    job = app.cases.create_run(
        InvestigationRequest(
            case=case,
            options=InvestigationStart(mode="agent"),
            policy=app.policies.get_policy(),
            model_name=config.llm.model,
        )
    )
    app.cases.update_job(job.id, "running")
    tools = build_investigation_tools(config.data_dir, job.id)
    tools.seed_manual_evidence()
    return job, tools


def test_source_follow_only_enables_actually_verified_feed(system, monkeypatch):
    config, app = system
    job, tools = running_investigation(config, app)

    class Feed:
        def fetch(self, url):
            return FetchedDocument(
                url,
                b"<rss><channel><item><title>LLM drug discovery</title>"
                b"<link>https://news.example/article</link></item></channel></rss>",
                "application/rss+xml",
                200,
            )

    tools.fetcher = Feed()
    page = tools.fetch_page("https://news.example/feed")
    assert len(page.feed_entries) == 1
    proposal = app.changes.propose(
        job.id,
        MaintenanceChange(
            kind="source_follow",
            reason="Verified feed",
            evidence_ids=[page.id],
            source=SourceEdit(
                name="News",
                homepage_url="https://news.example",
                endpoint_url=page.url,
                access_method="rss",
                connector_key="rss-v1",
            ),
        ),
    )
    assert not app.sources.active_feeds()
    app.cases.update_job(job.id, "complete", tools.build_report())
    app.changes.resolve(proposal.id, True)
    feed = app.sources.active_feeds()[0]
    assert feed.status == "watch" and feed.dataset == "main"
    assert feed.endpoint_url == page.url
    with sqlite3.connect(config.data_dir / "discovery.sqlite3") as db:
        assert (
            db.execute("SELECT enabled FROM source_endpoints WHERE id=?", (feed.endpoint_id,)).fetchone()[0]
            == 1
        )
    provider = build_daily_literature_discovery(
        config,
        __import__(
            "agent4pharma.composition", fromlist=["open_application_storage"]
        ).open_application_storage(config.data_dir),
    )
    try:
        assert feed.id in provider.providers
    finally:
        provider.close()
        provider.store.close()


def test_v4_upgrade_keeps_manual_case_and_backup(tmp_path):
    from agent4pharma.composition import open_application_storage
    from agent4pharma.infrastructure.persistence.sqlite.migrations.v001 import SCHEMA
    from agent4pharma.infrastructure.persistence.sqlite.migrations.v002 import MIGRATION_2
    from agent4pharma.infrastructure.persistence.sqlite.migrations.v003 import MIGRATION_3
    from agent4pharma.infrastructure.persistence.sqlite.migrations.v004 import MIGRATION_4

    with sqlite3.connect(tmp_path / "discovery.sqlite3") as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO schema_version(version) VALUES (1)")
        db.commit()
        for migration in [MIGRATION_2, MIGRATION_3, MIGRATION_4]:
            db.executescript(migration)
        db.execute(
            "INSERT INTO validation_cases(id,input_json,revision,created_at,updated_at) VALUES (?,?,?,?,?)",
            (
                "old-case",
                '{"reference":"Original user input","text":"Keep this"}',
                1,
                "2026-10-29",
                "2026-10-29",
            ),
        )
    store = open_application_storage(tmp_path)
    try:
        assert store.db.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 13
        assert "Keep this" in store.db.execute("SELECT input_json FROM validation_cases").fetchone()[0]
    finally:
        store.close()
    backup = next((tmp_path / "backups").glob("*pre-v5-*.sqlite3"))
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 4
        assert db.execute("SELECT count(*) FROM validation_cases").fetchone()[0] == 1
