from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from agent4pharma.composition import build_daily_literature_discovery
from agent4pharma.infrastructure.persistence.sqlite.application_storage import SqliteApplicationStorage
from agent4pharma.modules.collection.errors import PaginationChanged, SourceError
from agent4pharma.modules.materials.collection_records import CollectedRecord, CollectionPage, SourceResponse
from agent4pharma.runtime.scheduling import due_days
from agent4pharma.runtime.settings import ApplicationSettings


class Pages:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def page(self, start, end, query, cursor):
        self.calls.append(cursor)
        if self.fail and cursor:
            raise SourceError("offline")
        index = (cursor or {}).get("offset", 0)
        record = CollectedRecord(
            "biorxiv",
            f"id-{index}",
            f"Paper {index}",
            "LLM agents enable drug discovery. " * 4,
            identifiers={"doi": f"10.1/paper{index}"},
            version="1",
        )
        return CollectionPage(
            [record],
            {"offset": 1} if not cursor else None,
            [SourceResponse(b"{}", "https://example.org", "application/json")],
            2,
            "date-only",
        )


@pytest.fixture
def setup(tmp_path):
    config = ApplicationSettings(data_dir=tmp_path, sources={"biorxiv": {"query": ""}})
    store = SqliteApplicationStorage(tmp_path)
    yield config, store
    store.close()


def test_resume_and_idempotency(setup, evaluate_offline):
    config, store = setup
    pages = Pages(fail=True)
    flow = build_daily_literature_discovery(config, store, {"biorxiv": pages})
    first = flow.run(date(2026, 10, 28))
    assert first["status"] == "partial"
    assert first["unique_materials"] == 1
    pages.fail = False
    second = flow.run(date(2026, 10, 28))
    second = evaluate_offline(config, store, second["batch_id"])
    assert second["status"] == "complete"
    assert second["unique_materials"] == 2
    assert pages.calls == [None, {"offset": 1}, {"offset": 1}]
    third = flow.run(date(2026, 10, 28))
    assert len(third["candidates"]) == 2
    assert len(pages.calls) == 3
    assert store.db.execute("SELECT count(*) FROM source_records").fetchone()[0] == 2


def test_overlap_does_not_rerecommend(setup, evaluate_offline):
    config, store = setup
    flow = build_daily_literature_discovery(config, store, {"biorxiv": Pages()})
    first = flow.run(date(2026, 10, 27))
    first = evaluate_offline(config, store, first["batch_id"])
    second = flow.run(date(2026, 10, 28))
    assert len(first["candidates"]) == 2
    assert second["candidates"] == []
    assert store.db.execute("SELECT count(*) FROM materials").fetchone()[0] == 2


def test_page_budget_resume(setup):
    config, store = setup
    config.page_budget = 1
    flow = build_daily_literature_discovery(config, store, {"biorxiv": Pages()})
    assert flow.run(date(2026, 10, 28))["status"] == "partial"
    assert flow.run(date(2026, 10, 28))["coverage_status"] == "complete"


def test_zero_results_vs_source_failure(setup):
    config, store = setup

    class Empty:
        def page(self, *args):
            return CollectionPage([], None, [], 0, "test")

    class Failed:
        def page(self, *args):
            raise SourceError("unavailable")

    assert (
        build_daily_literature_discovery(config, store, {"biorxiv": Empty()}).run(date(2026, 10, 27))[
            "status"
        ]
        == "complete"
    )
    failed = build_daily_literature_discovery(config, store, {"biorxiv": Failed()}).run(date(2026, 10, 28))
    assert failed["status"] == "failed"
    assert failed["sources"][0]["error"] == "unavailable"


def test_atomic_page_checkpoint(setup):
    config, store = setup
    store.seed_sources(config)
    run_id, batch_id = store.create_run("2026-10-28", config)
    job = store.source_job(run_id, "biorxiv", {"source": "biorxiv"})
    page = Pages().page(None, None, None, None)
    page.records.append(CollectedRecord("biorxiv", "bad", "Bad", identifiers={"bad": object()}))
    with pytest.raises(TypeError):
        store.commit_page(batch_id, job["id"], page, "unused")
    assert store.db.execute("SELECT count(*) FROM source_records").fetchone()[0] == 0
    assert store.job(job["id"])["result"] is None


def test_schedule_0800_and_missed_days(setup, evaluate_offline):
    config, store = setup
    tz = ZoneInfo("Asia/Shanghai")
    assert due_days(config, store, datetime(2026, 10, 29, 7, 59, tzinfo=tz)) == [date(2026, 10, 27)]
    report = build_daily_literature_discovery(config, store, {"biorxiv": Pages()}).run(date(2026, 10, 27))
    evaluate_offline(config, store, report["batch_id"])
    assert due_days(config, store, datetime(2026, 10, 30, 8, tzinfo=tz)) == [
        date(2026, 10, 28),
        date(2026, 10, 29),
    ]


def test_exact_identity_and_version_update(setup, evaluate_offline):
    config, store = setup
    provider = Pages()
    flow = build_daily_literature_discovery(config, store, {"biorxiv": provider})
    flow.run(date(2026, 10, 27))
    original = provider.page

    def revised(*args):
        page = original(*args)
        for record in page.records:
            record.version = "2"
        return page

    provider.page = revised
    result = flow.run(date(2026, 10, 28))
    result = evaluate_offline(config, store, result["batch_id"])
    assert len(result["candidates"]) == 2
    assert store.db.execute("SELECT count(*) FROM materials").fetchone()[0] == 2
    assert store.db.execute("SELECT count(*) FROM source_records").fetchone()[0] == 4
    kinds = store.db.execute(
        "SELECT DISTINCT kind FROM discovery_batch_items WHERE batch_id=?", (result["batch_id"],)
    ).fetchall()
    assert [r[0] for r in kinds] == ["version_update"]


def test_changing_pagination_restarts_without_duplicate_candidates(setup, evaluate_offline):
    config, store = setup
    provider = Pages()
    original = provider.page
    changed = [False]

    def page(*args):
        if args[-1] and not changed[0]:
            changed[0] = True
            raise PaginationChanged("Results changed")
        return original(*args)

    provider.page = page
    flow = build_daily_literature_discovery(config, store, {"biorxiv": provider})
    first = flow.run(date(2026, 10, 28))
    assert first["status"] == "partial"
    assert first["sources"][0]["result"] is None
    second = flow.run(date(2026, 10, 28))
    second = evaluate_offline(config, store, second["batch_id"])
    assert second["status"] == "complete"
    assert len(second["candidates"]) == 2


def test_changed_policy_can_reassess_known_records(setup, evaluate_offline):
    config, store = setup
    flow = build_daily_literature_discovery(config, store, {"biorxiv": Pages()})
    first = flow.run(date(2026, 10, 28))
    first = evaluate_offline(config, store, first["batch_id"])
    assert len(first["candidates"]) == 2
    config.policy_version = "new-policy"
    from agent4pharma.modules.discovery.screening_policy import ScreeningPolicy

    config.screening_json = ScreeningPolicy(drug_terms=["unmatched pharmaceutical task"]).model_dump_json()
    second = flow.run(date(2026, 10, 28))
    assert second["batch_id"] != first["batch_id"]
    assert not second["candidates"]
    rows = store.items(second["batch_id"])["items"]
    # 关键词不再过滤已采集资料；未启用本地判别时全部进入情报队列。
    assert {row["state"] for row in rows} == {"awaiting_intelligence"}
    assert {row["kind"] for row in rows} == {"policy_reevaluation"}
    assert all(row["assessment"]["method"] == "semantic_passthrough" for row in rows)
