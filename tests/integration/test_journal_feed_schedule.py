"""期刊 RSS 元数据计划走日常持久化流程，不依赖详情页访问。"""

import json
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from agent4pharma.composition import (
    build_daily_literature_discovery,
    build_frozen_collection_workflow,
    open_application_storage,
    save_collection_plan,
)
from agent4pharma.infrastructure.persistence.sqlite.batch_intelligence import BatchIntelligenceRepository
from agent4pharma.infrastructure.web.public_fetch import PublicPageFetcher
from agent4pharma.modules.maintenance.repository import FetchedDocument
from agent4pharma.modules.sources.collection_plans import (
    ArticleRules,
    CollectionPlan,
    DiscoveryEntry,
    ExtractField,
)
from agent4pharma.runtime.scheduling import due_days
from agent4pharma.runtime.settings import ApplicationSettings


def definition():
    return CollectionPlan(
        source_id="journal-test",
        name="Test journal",
        homepage_url="https://example.org/journal",
        allowed_hosts=["example.org"],
        acquisition_mode="feed_metadata",
        entries=[DiscoveryEntry(kind="rss", url="https://example.org/feed")],
        article=ArticleRules(body=ExtractField(selectors=["article"])),
    )


def test_feed_metadata_is_persisted_and_frozen_without_detail_fetch(tmp_path, monkeypatch):
    raw = b"""<rss><channel><item><title>AI designs proteins</title>
    <link>https://example.org/article/one</link><description>Generative protein design</description>
    <affiliation>Google DeepMind</affiliation>
    <pubDate>2026-10-29T04:00:00Z</pubDate></item><item><title>Unknown date</title>
    <link>https://example.org/article/two</link></item></channel></rss>"""
    requests = []

    def fetch(self, url):
        requests.append(url)
        assert url == "https://example.org/feed"
        return FetchedDocument(url, raw, "application/rss+xml", 200)

    monkeypatch.setattr(PublicPageFetcher, "fetch", fetch)
    store = open_application_storage(tmp_path)
    workflow = resumed = None
    try:
        save_collection_plan(definition().model_dump_json(), tmp_path)
        config = ApplicationSettings(data_dir=tmp_path, sources={"pubmed": {}})
        workflow = build_daily_literature_discovery(config, store, configured_only=True)
        result = workflow.run(date(2026, 10, 29))
        assert result["coverage_status"] == "complete"
        assert result["unique_materials"] == 2
        assert result["awaiting_intelligence"] == 2
        details = BatchIntelligenceRepository(tmp_path).selection_details(result["batch_id"])
        assert sorted(d.affiliations for d in details.values()) == [[], ["Google DeepMind"]]
        now = datetime(2026, 10, 30, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert due_days(config, store, now, collect_only=True) == []
        assert due_days(config, store, now) == [date(2026, 10, 29)]
        assert requests == ["https://example.org/feed"]
        frozen = store.job(result["batch_id"])["request"]["policy"]
        assert frozen["sources"]["journal-test"]["connector_version"] == "rss-v4"
        assert frozen["sources"]["journal-test"]["feed_parser_version"] == "rss-v4"
        resumed = build_frozen_collection_workflow(config, store, result["batch_id"])
        assert resumed.run(date(2026, 10, 29))["unique_materials"] == 2
        assert len(requests) == 1
        from agent4pharma.entrypoints.cli.argument_parser import build_parser
        from agent4pharma.entrypoints.cli.discovery_commands import execute

        args = build_parser().parse_args(["run", "--date", "2026-10-29", "--collect-only"])
        args.configured_only = True
        args.source = ["journal-test"]
        execute(args, config.model_copy(deep=True), store)
        assert len(requests) == 1
        historical = json.loads(json.dumps(store.job(result["batch_id"])))
        del historical["request"]["policy"]["sources"]["journal-test"]["feed_parser_version"]
        with pytest.raises(ValueError, match="提取器版本不匹配"):
            build_frozen_collection_workflow(
                config, SimpleNamespace(job=lambda _: historical), result["batch_id"]
            )
        historical["request"]["policy"]["sources"]["journal-test"]["feed_parser_version"] = "rss-v3"
        with pytest.raises(ValueError, match="提取器版本不匹配"):
            build_frozen_collection_workflow(
                config, SimpleNamespace(job=lambda _: historical), result["batch_id"]
            )
    finally:
        if workflow:
            workflow.close()
        if resumed:
            resumed.close()
        store.close()


def test_metadata_mode_rejects_html_and_preserves_old_plan_serialization():
    plan = definition()
    old = plan.model_copy(update={"acquisition_mode": "article_details"})
    assert "acquisition_mode" not in json.loads(old.model_dump_json())
    assert "feed_metadata" in plan.model_dump_json()
    assert "feed_date_policy" not in plan.model_dump_json()
    with pytest.raises(ValueError, match="feed_date_policy"):
        CollectionPlan.model_validate({**old.model_dump(), "feed_date_policy": "current_feed"})
    with pytest.raises(ValueError, match="feed_metadata"):
        CollectionPlan.model_validate(
            {
                **plan.model_dump(),
                "entries": [{"url": "https://example.org/", "kind": "html", "link_selector": "a"}],
            }
        )


@pytest.mark.parametrize("date_policy,expected_count", [("publication_window", 0), ("current_feed", 1)])
def test_current_feed_policy_collects_older_issue_and_freezes_without_duplicates(
    tmp_path, monkeypatch, date_policy, expected_count
):
    raw = b"""<rss><channel><item><title>Weekly issue paper</title>
        <link>https://example.org/paper</link><doi>10.1234/weekly</doi>
        <description>A weekly issue paper</description>
        <pubDate>2026-10-01T08:00:00Z</pubDate></item></channel></rss>"""
    requests = []

    def fetch(self, url):
        requests.append(url)
        return FetchedDocument(url, raw, "application/rss+xml", 200)

    monkeypatch.setattr(PublicPageFetcher, "fetch", fetch)
    plan = definition().model_copy(update={"feed_date_policy": date_policy})
    store = open_application_storage(tmp_path)
    save_collection_plan(plan.model_dump_json(), tmp_path)
    config = ApplicationSettings(data_dir=tmp_path, sources={"pubmed": {}}, initial_days=1)
    workflow = build_daily_literature_discovery(config, store, configured_only=True)
    resumed = None
    try:
        result = workflow.run(date(2026, 11, 8))
        assert result["unique_materials"] == expected_count
        assert workflow.run(date(2026, 11, 8))["unique_materials"] == expected_count
        assert len(requests) == 1
        snapshot = store.job(result["batch_id"])["request"]["policy"]
        frozen_plan = json.loads(snapshot["sources"]["journal-test"]["plan_json"])
        assert frozen_plan.get("feed_date_policy", "publication_window") == date_policy
        save_collection_plan(definition().model_dump_json(), tmp_path, expected_hash=plan.fingerprint())
        resumed = build_frozen_collection_workflow(config, store, result["batch_id"])
        assert resumed.providers["journal-test"].date_policy == date_policy
        if expected_count:
            next_day = workflow.run(date(2026, 11, 9))
            assert next_day["unique_materials"] == 1
            assert len(requests) == 2
            import sqlite3

            with sqlite3.connect(tmp_path / "discovery.sqlite3") as db:
                assert db.execute("SELECT count(*) FROM materials").fetchone()[0] == 1
                assert db.execute("SELECT count(*) FROM source_records").fetchone()[0] == 1
                record = json.loads(db.execute("SELECT metadata FROM source_records").fetchone()[0])
                assert record["published"] == "2026-10-01T08:00:00+00:00"
            assert "publication_window_not_applied" in result["sources"][0]["result"]["date_basis"]
    finally:
        workflow.close()
        if resumed:
            resumed.close()
        store.close()
