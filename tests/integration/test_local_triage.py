"""本地预选的真实持久化链路使用离线模型替身，禁止付费网络。"""

from datetime import date

import pytest

from agent4pharma.composition import (
    build_daily_literature_discovery,
    open_application_storage,
    save_collection_plan,
)
from agent4pharma.infrastructure.connectors.rss_signals import bibliographic_only
from agent4pharma.infrastructure.persistence.sqlite.rss_triage_repository import RssTriageRepository
from agent4pharma.infrastructure.web.public_fetch import PublicPageFetcher
from agent4pharma.modules.discovery.local_triage import LocalTriage
from agent4pharma.modules.discovery.triage_contracts import ModelDecision, TriageInput, TriagePolicy
from agent4pharma.modules.maintenance.repository import FetchedDocument
from agent4pharma.modules.sources.collection_plans import (
    ArticleRules,
    CollectionPlan,
    DiscoveryEntry,
    ExtractField,
    RssExtractionProfile,
)
from agent4pharma.runtime.settings import ApplicationSettings, LocalTriageSettings


class OfflineModel:
    def __init__(self, fail=False):
        self.calls, self.fail = 0, fail

    def classify(self, inputs, policy):
        self.calls += len(inputs)
        if self.fail:
            raise RuntimeError("private diagnostic")
        options = {
            "AI proteins": ("analyze", {"analyze": 0.9, "exclude": 0.1}),
            "Black holes": ("exclude", {"analyze": 0.03, "exclude": 0.97}),
            "Unclear": ("exclude", {"analyze": 0.4, "exclude": 0.6}),
        }
        return [
            ModelDecision(
                choice=options[item.title][0], probabilities=options[item.title][1], input_tokens=100
            )
            for item in inputs
        ]


def setup_run(tmp_path, monkeypatch, model):
    store = open_application_storage(tmp_path)
    profile = RssExtractionProfile(
        journal_name="Journal",
        text_fields=["description"],
        inspected_at="2026-10-30T00:00:00Z",
        evidence_sha256="snapshot",
    )
    plan = CollectionPlan(
        source_id="journal",
        name="Journal",
        homepage_url="https://example.org/",
        allowed_hosts=["example.org"],
        acquisition_mode="feed_metadata",
        rss_profile=profile,
        entries=[DiscoveryEntry(kind="rss", url="https://example.org/feed")],
        article=ArticleRules(body=ExtractField(selectors=["article"])),
    )
    save_collection_plan(plan.model_dump_json(), tmp_path)
    raw = (
        b'<rss xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/"><channel>'
        + b"".join(
            (
                f"<item><title>{title}</title><link>https://example.org/{i}</link>"
                f"<description>{text}</description><prism:doi>10.0000/{title}</prism:doi></item>"
            ).encode()
            for i, (title, text) in enumerate(
                [
                    ("AI proteins", "Generative proteins"),
                    ("Black holes", "Physics"),
                    ("Unclear", ""),
                    ("AI proteins", "Generative proteins"),
                ]
            )
        )
        + b"</channel></rss>"
    )
    monkeypatch.setattr(
        PublicPageFetcher, "fetch", lambda self, url: FetchedDocument(url, raw, "application/rss+xml", 200)
    )
    config = ApplicationSettings(
        data_dir=tmp_path, sources={"pubmed": {}}, triage=LocalTriageSettings(enabled=True)
    )
    flow = build_daily_literature_discovery(config, store, configured_only=True)
    flow.triage = LocalTriage(RssTriageRepository(tmp_path), model, "model-rev", TriagePolicy())
    return store, flow


def test_full_collection_triage_handoff_and_idempotent_resume(tmp_path, monkeypatch):
    model = OfflineModel()
    store, flow = setup_run(tmp_path, monkeypatch, model)
    try:
        report = flow.run(date(2026, 10, 29))
        assert report["coverage_status"] == "complete" and report["awaiting_triage"] == 0
        assert model.calls == 3  # 同内容不同来源链接，不重复推理。
        from agent4pharma.infrastructure.persistence.sqlite.discovery_run_view import DiscoveryRunReader

        snapshot = DiscoveryRunReader(tmp_path).snapshot(report["batch_id"])
        assert len(snapshot.articles) == 4
        assert len({a.material_id for a in snapshot.articles}) == 3
        assert len([a for a in snapshot.articles if a.route == "analyze"]) == 2
        assert len({a.triage_id for a in snapshot.articles}) == 3
        assert snapshot.sources[0].records == 4
        assert snapshot.sources[0].status == "complete"
        assert report["awaiting_intelligence"] == 2
        decisions = RssTriageRepository(tmp_path).decisions(report["batch_id"])
        assert {decision.route for decision in decisions} == {"analyze", "exclude"}
        states = RssTriageRepository(tmp_path).batches()[0]["states"]
        assert states == {"awaiting_intelligence": 2, "not_selected": 2}
        flow.run(date(2026, 10, 29))
        assert model.calls == 3
        assert list((tmp_path / "backups").glob("discovery-pre-v9-*"))
    finally:
        flow.close()
        store.close()


def test_model_failure_is_resumable_and_never_exclusion(tmp_path, monkeypatch):
    model = OfflineModel(fail=True)
    store, flow = setup_run(tmp_path, monkeypatch, model)
    try:
        with pytest.raises(RuntimeError, match="可续跑"):
            flow.run(date(2026, 10, 29))
        batch = RssTriageRepository(tmp_path).batches()[0]
        assert batch["states"] == {"awaiting_triage": 4}
        assert not RssTriageRepository(tmp_path).decisions(batch["batch_id"])
        from agent4pharma.infrastructure.persistence.sqlite.discovery_run_view import DiscoveryRunReader

        snapshot = DiscoveryRunReader(tmp_path).snapshot(batch["batch_id"])
        assert len(snapshot.articles) == 4
        assert all(a.triage_error and not a.route for a in snapshot.articles)
        assert snapshot.sources[0].status == "complete"
        model.fail = False
        assert flow.run(date(2026, 10, 29))["awaiting_triage"] == 0
    finally:
        flow.close()
        store.close()


def test_run_coverage_distinguishes_empty_partial_failed_and_unstarted(tmp_path):
    from agent4pharma.infrastructure.persistence.sqlite.discovery_run_view import DiscoveryRunReader

    store = open_application_storage(tmp_path)
    try:
        config = ApplicationSettings(
            data_dir=tmp_path, sources={name: {} for name in ("empty", "failed", "partial", "unstarted")}
        )
        run, batch = store.create_run("2026-10-30", config)
        for name, status, result in [
            ("empty", "complete", {"cursor": None}),
            ("failed", "failed", None),
            ("partial", "partial", {"cursor": "next"}),
        ]:
            job = store.source_job(run, name, {"source": name})
            store.set_job(
                job["id"], status, result=result, error="safe error" if status != "complete" else None
            )
        snapshot = DiscoveryRunReader(tmp_path).snapshot(batch)
        sources = {s.source_id: s for s in snapshot.sources}
        assert sources["empty"].records == 0
        assert sources["failed"].records is None
        assert sources["partial"].status == "partial"
        assert sources["unstarted"].status == "not_started" and sources["unstarted"].records is None
        assert snapshot.batches == [batch]  # 没有成功粗筛的批次也能发现。
    finally:
        store.close()


def test_no_summary_generated_from_bibliographic_metadata():
    assert bibliographic_only("Publication date: October 2026 Source: Cell Author(s): Author Name")
    assert bibliographic_only("Science, Volume 394, Issue 6823, Page 492-492, October 2026. <br/>")
    assert not bibliographic_only("A neural network designs experimentally validated proteins.")


def test_model_policy_and_extraction_versions_change_cache_identity():
    item = TriageInput(material_ref="m", source_record_id="a", source_id="s", title="T", journal="J", url="")
    changed = item.model_copy(
        update={"source_record_id": "b", "source_id": "other", "url": "https://example.org"}
    )
    assert item.content_hash() == changed.content_hash()
    assert item.content_hash() != item.model_copy(update={"extraction_version": "v2"}).content_hash()
    assert TriagePolicy().content_hash() != TriagePolicy(instructions="Different scope").content_hash()


def test_binary_only_has_two_options_and_ties_do_not_enter():
    policy = TriagePolicy()
    assert set(policy.criteria) == {"analyze", "exclude"}
    assert "reject_probability" not in policy.model_dump()
    with pytest.raises(ValueError, match="新版预选"):
        TriagePolicy(reject_probability=0.95)
    item = TriageInput(material_ref="m", source_record_id="r", source_id="s", title="T", journal="J", url="")
    model = ModelDecision(choice="analyze", probabilities={"analyze": 0.5, "exclude": 0.5}, input_tokens=10)
    decision = LocalTriage(None, None, "model", policy).decision(item, model)
    assert decision.route == "exclude"
    assert "insufficient" not in decision.result.probabilities.model_dump()


def test_non_rss_record_without_keyword_hit_still_uses_local_decider(tmp_path):
    """非 RSS 资料也必须交本地判别；原生摘要作为判别文本，关键词不参与过滤。"""
    from agent4pharma.infrastructure.persistence.sqlite.coarse_screening import assess_coarse
    from agent4pharma.modules.materials.collection_records import CollectedRecord

    class CaptureModel:
        def __init__(self):
            self.seen = []

        def classify(self, inputs, policy):
            self.seen.extend(inputs)
            return [
                ModelDecision(
                    choice="analyze",
                    probabilities={"analyze": 0.8, "exclude": 0.2},
                    input_tokens=10,
                )
                for _ in inputs
            ]

    store = open_application_storage(tmp_path)
    try:
        config = ApplicationSettings(
            data_dir=tmp_path,
            sources={"pubmed": {"query": ""}},
            triage=LocalTriageSettings(enabled=True),
        )
        _, batch = store.create_run("2026-10-30", config)
        with store.db:
            for record in (
                CollectedRecord(
                    source="pubmed",
                    external_id="pmid-1",
                    title="Black holes",
                    abstract="A galaxy survey without any artificial intelligence.",
                ),
                CollectedRecord(source="pubmed", external_id="pmid-2", title="Protein folding"),
            ):
                stored = store.materials.ingest_record(record, [])
                store.discovery.register_record(batch, stored, config.snapshot())
        assess_coarse(store.db, batch, config)
        assert {row["state"] for row in store.items(batch)["items"]} == {"awaiting_triage"}

        inputs = RssTriageRepository(tmp_path).inputs(batch)
        by_title = {item.title: item for item in inputs}
        assert by_title["Black holes"].information_status == "abstract_available"
        assert [(field.field, field.text) for field in by_title["Black holes"].fields] == [
            ("abstract", "A galaxy survey without any artificial intelligence.")
        ]
        assert by_title["Protein folding"].information_status == "title_only"

        model = CaptureModel()
        LocalTriage(RssTriageRepository(tmp_path), model, "model-rev", TriagePolicy()).run(batch)
        rows = {row["metadata"]["title"]: row for row in store.items(batch)["items"]}
        assert {row["state"] for row in rows.values()} == {"awaiting_intelligence"}
        # 原生摘要可直接作为判断证据；只有标题/Feed 简介才强制原文复判。
        assert rows["Black holes"]["assessment"]["require_primary_read"] is False
        assert rows["Protein folding"]["assessment"]["require_primary_read"] is True
    finally:
        store.close()


def test_historical_three_way_decisions_are_still_readable():
    from agent4pharma.modules.discovery.triage_contracts import TriageDecision

    policy = TriagePolicy(
        version="ai-biology-rss-v2",
        criteria={"analyze": "yes", "exclude": "no", "insufficient": "?"},
        reject_probability=0.95,
    )
    item = TriageInput(material_ref="m", source_record_id="r", source_id="s", title="T", journal="J", url="")
    model = ModelDecision(
        choice="insufficient",
        probabilities={"analyze": 0.1, "exclude": 0.2, "insufficient": 0.7},
        input_tokens=10,
    )
    old = LocalTriage(None, None, "model", policy).decision(item, model)
    assert TriageDecision.model_validate_json(old.model_dump_json()).route == "needs_evidence"
