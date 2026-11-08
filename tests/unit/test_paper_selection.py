from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from agent4pharma.infrastructure.persistence.chaoslib_access import ChaosLibAccess
from agent4pharma.infrastructure.persistence.paper_selection_repository import PaperSelectionRepository
from agent4pharma.infrastructure.web.chaoslib_downloader import ChaosLibDownloader
from agent4pharma.modules.discovery.intelligence_contracts import (
    IntelligenceAssessment,
    IntelligenceEvidence,
    IntelligencePolicy,
    IntelligencePolicyVersion,
    IntelligenceRequest,
    IntelligenceTask,
)
from agent4pharma.modules.discovery.paper_selection import PaperSelectionItem, PaperSelectionRun
from agent4pharma.modules.materials.paper_downloads import PaperDownloadResult


def selection(tmp_path, count=8):
    policy = IntelligencePolicyVersion(
        id="policy",
        version=1,
        status="published",
        content_hash="hash",
        definition=IntelligencePolicy(
            instructions="AI biology screening instructions " * 3, decision_mode="single_pass"
        ),
    )
    items = [
        PaperSelectionItem(
            journal="Nature Methods" if i < 6 else "Other Journal",
            request=IntelligenceRequest(
                material_ref=f"m{i}",
                mode="production",
                policy=policy,
                model="offline",
                evidence=[
                    IntelligenceEvidence(
                        id=f"r{i}", title="AI biology", text="AI biology", doi=f"10.1038/p{i}"
                    )
                ],
            ),
        )
        for i in range(count)
    ]
    run = PaperSelectionRun(
        id="selection-" + "a" * 24, batch_id="batch", created_at="2026-11-01T00:00:00Z", items=items
    )
    store = PaperSelectionRepository(tmp_path)
    store.save(run)
    return run, store


def test_latest_filters_batch_before_decoding_unrelated_runs(tmp_path, monkeypatch):
    run, store = selection(tmp_path)
    other = run.model_copy(update={"id": "selection-" + "b" * 24, "batch_id": "other", "revision": 0})
    store.save(other)
    original = PaperSelectionRepository.get
    reads = []

    def tracked(self, identity):
        reads.append(identity)
        return original(self, identity)

    monkeypatch.setattr(PaperSelectionRepository, "get", tracked)
    assert [r.id for r in store.latest("batch")] == [run.id]
    assert reads == [run.id]


def test_manual_selection_preserves_model_ranking_and_expands_only_manual_association(tmp_path):
    from agent4pharma.composition import manually_select_paper
    from agent4pharma.modules.discovery.paper_selection import (
        require_pdf_association,
        require_shortlisted_paper,
    )
    from agent4pharma.workflows.paper_selection_page import selection_page

    run, store = selection(tmp_path, 12)
    run.phase = "fulltexts"
    for index, item in enumerate(run.items, 1):
        item.local_rank = index
        item.state = "deferred"
    store.save(run)
    manually_select_paper(tmp_path, run.id, "r11")
    selected = store.get(run.id)
    assert selected.items[11].local_rank == 12
    assert selected.items[11].rank is None
    assert selected.items[11].state == "deferred"
    assert selected.items[11].human_recommendation is None
    assert require_pdf_association(selected, "r11").manual_selected_at
    with pytest.raises(ValueError):
        require_shortlisted_paper(selected, "r11")
    revision = selected.revision
    manually_select_paper(tmp_path, run.id, "r11")
    assert store.get(run.id).revision == revision
    view = selection_page(store, run.batch_id)["paper_selections"][0]
    assert any(row["record_id"] == "r11" and row["can_associate_pdf"] for row in view["items"])
    with pytest.raises(ValueError):
        manually_select_paper(tmp_path, run.id, "unknown")


def test_processed_identity_matches_doi_across_sources_but_not_failed_or_debug(tmp_path):
    from agent4pharma.modules.discovery.processed_papers import paper_keys, processed_paper_keys
    from agent4pharma.workflows.selected_paper_download import download_request

    run, _ = selection(tmp_path, 2)
    request = download_request(run, run.items[0])
    result = PaperDownloadResult(
        id="ok",
        request=request,
        status="downloaded",
        identity_match=True,
        sha256="a" * 64,
        pages=1,
        created_at="2026-11-08T00:00:00Z",
    )
    alternate = run.items[0].request.model_copy(deep=True)
    alternate.material_ref = "another-source"
    alternate.evidence[0].id = "another-version"
    alternate.evidence[0].doi = "https://doi.org/10.1038/P0"
    assert paper_keys(alternate) & processed_paper_keys([result])
    assert not paper_keys(run.items[1].request) & processed_paper_keys([result])
    assert not processed_paper_keys([result.model_copy(update={"status": "failed"})])
    assert not processed_paper_keys([result.model_copy(update={"identity_match": False})])
    debug = result.model_copy(update={"request": request.model_copy(update={"decision_id": "debug:run"})})
    assert not processed_paper_keys([debug])


def test_scoped_ranking_reads_exact_history_without_other_pools(tmp_path, monkeypatch):
    import agent4pharma.composition as composition
    from agent4pharma.infrastructure.persistence.sqlite.rss_triage_repository import RssTriageRepository

    run, store = selection(tmp_path)
    run.phase = "fulltexts"
    store.save(run)
    other = run.model_copy(update={"id": "selection-" + "b" * 24, "revision": 0})
    store.save(other)
    monkeypatch.setattr(RssTriageRepository, "batches", lambda self, batch_id="": [{"batch_id": "batch"}])
    monkeypatch.setattr(RssTriageRepository, "policy_snapshot", lambda *args: None)

    def forbidden(*args):
        pytest.fail("scoped ranking must not read decisions, downloads, sources or all historical pools")

    monkeypatch.setattr(composition, "paper_selection_status", forbidden)
    monkeypatch.setattr(composition, "build_collection_plan_catalog", forbidden)
    monkeypatch.setattr(RssTriageRepository, "decisions", forbidden)
    page = composition.rss_triage_view(tmp_path, "batch", view="rank", run_id=run.id)
    assert [item["id"] for item in page["runs"]] == [run.id, other.id]
    assert page["paper_selections"][0]["id"] == run.id
    assert len(page["paper_selections"][0]["items"]) == len(run.items)
    assert page["decisions"] == []
    with pytest.raises(KeyError, match="不属于"):
        composition.rss_triage_view(tmp_path, "batch", view="rank", run_id="other-batch-run")


def test_preselection_reads_empty_discovery_batch_and_exact_old_batch(tmp_path):
    import agent4pharma.composition as composition
    from agent4pharma.infrastructure.persistence.sqlite.discovery_batch_repository import DiscoveryRepository
    from agent4pharma.infrastructure.persistence.sqlite.rss_triage_repository import RssTriageRepository
    from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction
    from agent4pharma.runtime.settings import ApplicationSettings

    config = ApplicationSettings(data_dir=tmp_path)
    composition.open_application_storage(tmp_path).close()
    with transaction(tmp_path) as db:
        repository = DiscoveryRepository(tmp_path, db)
        identities = [repository.create_run(f"day-{index}", config)[1] for index in range(52)]

    latest = composition.rss_triage_view(tmp_path, view="rank")
    assert latest["selected"] == identities[-1]
    assert latest["paper_selections"] == []
    assert latest["batches"][0]["states"] == {}
    assert len(latest["batches"]) == 50
    assert identities[0] not in {row["batch_id"] for row in latest["batches"]}
    for view in ("triage", "ingest", "rank"):
        page = composition.rss_triage_view(tmp_path, identities[0], view=view)
        assert page["selected"] == identities[0]
        assert identities[0] in {row["batch_id"] for row in page["batches"]}
        assert page["decisions"] == []
    assert composition.paper_selection_page(tmp_path, identities[0])["selected"] == identities[0]
    assert composition.paper_selection_downloads(tmp_path, identities[0]) == {"downloads": []}
    assert RssTriageRepository(tmp_path).decisions(identities[0]) == []
    for reader in (
        composition.rss_triage_view,
        composition.paper_selection_page,
        composition.paper_selection_downloads,
    ):
        with pytest.raises(KeyError, match="不存在"):
            reader(tmp_path, "missing-batch")


def test_compact_page_loads_only_exact_run_and_preserves_full_pool_statistics(tmp_path, monkeypatch):
    from agent4pharma.workflows.paper_selection_page import selection_page

    run, store = selection(tmp_path, count=24)
    run.phase = "fulltexts"
    run.review_pool_limit = 10
    for index, item in enumerate(run.items, 1):
        item.local_rank = index
        if index <= 5:
            item.rank = index
    store.save(run)
    other = run.model_copy(
        deep=True,
        update={
            "id": "selection-" + "b" * 24,
            "batch_id": "other",
            "revision": 0,
        },
    )
    store.save(other)
    original = run.model_dump_json()
    calls = []
    get = PaperSelectionRepository.get

    def read(self, identity):
        calls.append(identity)
        return get(self, identity)

    monkeypatch.setattr(PaperSelectionRepository, "get", read)
    page = selection_page(
        store, "batch", task_reader=lambda _: pytest.fail("finished runs do not read tasks")
    )
    assert calls == [run.id]
    assert [row["id"] for row in page["runs"]] == [run.id]
    current = page["paper_selections"][0]
    assert len(current["items"]) == 10
    assert sum(row["can_download"] for row in current["items"]) == 5
    assert current["progress"]["total"] == 24
    assert run.model_dump_json() == original
    with pytest.raises(KeyError, match="不属于"):
        selection_page(store, "batch", other.id)
    assert calls == [run.id]


def test_run_directory_cache_tracks_new_revisions_and_keeps_exact_batch(tmp_path):
    run, store = selection(tmp_path)
    assert store.summaries("batch")[0].phase == "abstracts"
    run.phase = "fulltexts"
    store.save(run)
    assert store.summaries("batch")[0].phase == "fulltexts"
    assert store.summaries("missing") == []


class Downloader:
    def __init__(self, status="manual_required"):
        self.requests = []
        self.status = status

    def download(self, request):
        self.requests.append(request)
        return PaperDownloadResult(
            id=str(len(self.requests)),
            request=request,
            status=self.status,
            reason="authentication_required",
            created_at="2026-11-01T00:00:00Z",
        )


def test_selection_stale_write_and_path_injection_rejected(tmp_path):
    run, store = selection(tmp_path, 1)
    stale = run.model_copy(deep=True)
    run.items[0].reason = "new revision"
    store.save(run)
    with pytest.raises(ValueError, match="版本"):
        store.save(stale)
    with pytest.raises(ValueError):
        store.get("../../private")


def test_shared_snapshot_preserves_exact_history_and_can_read_legacy_layout(tmp_path):
    import gzip
    import json

    run, store = selection(tmp_path, 8)
    for item in run.items:
        item.request.history = ["frozen history " * 3000]
    store.save(run)
    path = tmp_path / "paper-selections" / run.id / f"{run.revision:08d}.json.gz"
    payload = json.loads(gzip.decompress(path.read_bytes()))
    assert payload["format"] == "paper-selection-v2"
    references = [item["request"]["history"]["ref"] for item in payload["run"]["items"]]
    assert len(set(references)) == 1
    assert store.get(run.id) == run
    # 早期本地运行直接保存模型 JSON，必须继续可读，规范化不得改变输入。
    path.write_bytes(gzip.compress(run.model_dump_json().encode()))
    assert store.get(run.id) == run
    store.save(run)
    assert store.get(run.id) == run


def test_account_budget_and_block_survive_new_instances(tmp_path):
    now = datetime(2026, 11, 1, tzinfo=UTC)
    assert ChaosLibAccess(tmp_path).reserve(now) == ""
    assert ChaosLibAccess(tmp_path).reserve(now + timedelta(seconds=30)) == "attempt_interval"
    for minutes in (3, 6, 9, 12):
        assert ChaosLibAccess(tmp_path).reserve(now + timedelta(minutes=minutes)) == ""
    assert ChaosLibAccess(tmp_path).reserve(now + timedelta(minutes=15)) == ""
    assert ChaosLibAccess(tmp_path).status()["count"] == 6
    ChaosLibAccess(tmp_path).block("blocked_automation", now)
    assert ChaosLibAccess(tmp_path).reserve(now + timedelta(hours=2)) == "account_blocked_requires_manual"
    assert ChaosLibAccess(tmp_path).reserve(now + timedelta(days=2)) == "account_blocked_requires_manual"
    assert ChaosLibAccess(tmp_path).authentication(human=True, now=now) == "account_blocked_requires_manual"
    ChaosLibAccess(tmp_path).confirm_restored()
    assert ChaosLibAccess(tmp_path).reserve(now + timedelta(days=2)) == ""


def test_unsupported_publisher_returns_human_todo_without_login(tmp_path, monkeypatch):
    from agent4pharma.infrastructure.web import chaoslib_downloader as module

    monkeypatch.setattr(module, "load_settings", lambda _: pytest.fail("must not log in for known challenge"))
    run, _ = selection(tmp_path, 1)
    from agent4pharma.workflows.selected_paper_download import download_request

    request = download_request(run, run.items[0]).model_copy(update={"identifier": "10.1016/example"})
    assert ChaosLibDownloader(tmp_path).download(request).status == "manual_required"


def test_legacy_serialization_does_not_add_new_stage_or_score(tmp_path):
    run, _ = selection(tmp_path, 1)
    request = run.items[0].request.model_dump()
    assert not {"stage", "selection_ref", "stage_instructions"} & request.keys()
    assessment = IntelligenceAssessment(
        relevance="core", disposition="recommend", summary="s", reason="r", evidence_ids=["id"]
    )
    assert "priority_relevance" not in assessment.model_dump()


def test_live_selection_progress_does_not_freeze_or_mutate_snapshot(tmp_path):
    from agent4pharma.modules.discovery.paper_selection_view import selection_view

    run, store = selection(tmp_path, 3)
    request = run.items[0].request.model_copy(update={"stage": "abstract", "selection_ref": run.id})
    complete = IntelligenceTask(
        id="complete",
        status="complete",
        request=request,
        result=IntelligenceAssessment(
            relevance="core",
            disposition="recommend",
            summary="Archived result",
            reason="Archived result",
            evidence_ids=[request.evidence[0].id],
            priority_relevance=3,
        ),
    )
    running = IntelligenceTask(
        id="running",
        status="running",
        request=run.items[1].request.model_copy(update={"stage": "abstract", "selection_ref": run.id}),
    )
    foreign = complete.model_copy(
        update={
            "id": "foreign",
            "request": run.items[2].request.model_copy(
                update={"stage": "abstract", "selection_ref": "another-run"}
            ),
        }
    )
    result = selection_view(run, [complete, running, foreign])
    assert result["progress"] == {"total": 3, "completed": 1, "running": 1, "failed": 0, "remaining": 2}
    assert result["items"][1]["state"] == "abstract_running"
    assert not any(item["can_download"] for item in result["items"])
    assert run.items[0].state == "awaiting_abstract"
    assert store.get(run.id).items[0].state == "awaiting_abstract"


def test_manual_download_database_is_distinct_from_collection_source(tmp_path):
    from agent4pharma.modules.discovery.paper_selection_view import selection_view

    run, _ = selection(tmp_path, 4)
    for item, doi in zip(run.items, ["10.1038/paper", "10.1007/paper", "10.1016/paper", "10.9999/unknown"]):
        item.source_id = "pubmed"
        item.request.evidence[0].doi = doi
    items = selection_view(run)["items"]
    assert [item["download_database"] for item in items] == [
        "Nature（自然杂志）",
        "SpringerLink",
        "Science Direct",
        "",
    ]
    assert all(item["source_id"] == "pubmed" for item in items)


class LocalPaperModel:
    def __init__(self):
        self.classified = []
        self.ranked = []
        self.fail_rank = False

    def classify(self, inputs, policy):
        from agent4pharma.modules.discovery.triage_contracts import ModelDecision

        self.classified.extend(inputs)
        return [
            ModelDecision(choice="analyze", probabilities={"analyze": 0.8, "exclude": 0.2}, input_tokens=100)
            for _ in inputs
        ]

    def rank(self, inputs, instructions):
        self.ranked.extend(inputs)
        if self.fail_rank:
            raise RuntimeError("offline ranking failure")
        return [float(item.source_record_id[1:]) for item in inputs]


def local_run(tmp_path, count):
    from agent4pharma.modules.discovery.triage_contracts import TriagePolicy

    run, store = selection(tmp_path, count)
    run.selection_method = "local_decider_v1"
    run.missing_abstract_policy = "best_available"
    run.local_policy = TriagePolicy()
    run.local_revision = "fixed"
    run.ranking_instructions = "prioritize visible AI biology contributions"
    for item in run.items:
        item.abstract_evidence = IntelligenceEvidence(
            id=item.request.evidence[0].id,
            text="Actual AI biology abstract",
            abstract_status="found",
            status="available",
        )
    return run, store


def test_new_run_skips_successful_cross_batch_pdf_but_resume_keeps_frozen_pool(tmp_path, monkeypatch):
    from agent4pharma.composition import open_application_storage, screen_local_batch
    from agent4pharma.infrastructure.persistence.paper_download_repository import PaperDownloadRepository
    from agent4pharma.infrastructure.persistence.sqlite.batch_intelligence import BatchIntelligenceRepository
    from agent4pharma.modules.discovery.paper_selection import PaperSelectionDetails
    from agent4pharma.runtime.settings import ApplicationSettings, LocalTriageSettings
    from agent4pharma.workflows.selected_paper_download import download_request

    open_application_storage(tmp_path).close()
    old, store = local_run(tmp_path, 2)
    store.save(old)
    original = old.model_dump_json()
    request = download_request(old, old.items[0]).model_copy(update={"batch_id": "earlier-batch"})
    ledger = PaperDownloadRepository(tmp_path)
    ledger.save(
        PaperDownloadResult(
            id="previous-pdf",
            request=request,
            status="downloaded",
            identity_match=True,
            sha256="a" * 64,
            pages=1,
            created_at="2026-11-01T00:00:00Z",
        )
    )
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        BatchIntelligenceRepository,
        "selection_details",
        lambda *_: {
            item.request.evidence[0].id: PaperSelectionDetails(journal=item.journal) for item in old.items
        },
    )
    config = ApplicationSettings(data_dir=tmp_path, triage=LocalTriageSettings(model_revision="fixed"))
    new = screen_local_batch(config, old.batch_id, abstract_budget=0, review_budget=0)
    assert [item.request.evidence[0].id for item in new.items] == ["r1"]
    assert new.processed_record_ids == ["r0"]
    assert store.get(old.id).model_dump_json() == original
    ledger.save(
        PaperDownloadResult(
            id="later-pdf",
            request=download_request(new, new.items[0]),
            status="downloaded",
            identity_match=True,
            sha256="b" * 64,
            pages=1,
            created_at="2026-11-02T00:00:00Z",
        )
    )
    resumed = screen_local_batch(config, old.batch_id, run_id=new.id, abstract_budget=0, review_budget=0)
    assert resumed.items == new.items and resumed.processed_record_ids == ["r0"]


def test_local_ready_for_manual_download_finishes_automatic_scheduling(tmp_path):
    from agent4pharma.composition import paper_selection_automation_finished

    run, store = local_run(tmp_path, 2)
    store.save(run)
    assert not paper_selection_automation_finished(tmp_path, run.batch_id)
    run.phase = "fulltexts"
    run.items[0].state, run.items[0].rank = "shortlisted", 1
    store.save(run)
    assert paper_selection_automation_finished(tmp_path, run.batch_id)


class NewsModel:
    def __init__(self, selected=2):
        self.requests = []
        self.selected = selected
        self.invalid = False

    def compare(self, request):
        from agent4pharma.modules.discovery.news_selection import NewsComparisonCard, NewsComparisonResult

        self.requests.append(request.model_copy(deep=True))
        # Deliberately reverse the local order to prove the commercial comparison determines final rank.
        selected = [p.record_id for p in request.papers[-self.selected :]][::-1] if self.selected else []
        if self.invalid:
            selected = ["outside"]
        return NewsComparisonResult(
            selected_ids=selected,
            cards=[
                NewsComparisonCard(
                    record_id=p.record_id,
                    verdict="recommend" if p.record_id in selected else "defer",
                    relevance=3,
                    news_hook="新的可核实能力",
                    reason="跨论文比较后的下载价值",
                    evidence_quote="AI biology",
                    ai_quote="AI",
                    domain_quote="biology",
                    verification_question="全文中实验对照是否支持摘要中的能力？",
                )
                for p in request.papers
            ],
        )


def news_run(tmp_path, count=20):
    from agent4pharma.modules.discovery.news_preferences import default_institution_preferences
    from agent4pharma.modules.discovery.news_selection import (
        NEWS_COMPARISON_INSTRUCTIONS,
        NEWS_RANKING_INSTRUCTIONS,
        NEWS_SELECTION_VERSION,
    )

    run, store = local_run(tmp_path, count)
    run.news_selection_version = NEWS_SELECTION_VERSION
    run.institution_preferences = default_institution_preferences()
    run.comparison_instructions = NEWS_COMPARISON_INSTRUCTIONS
    run.comparison_thinking_mode = "disabled"
    run.ranking_instructions = NEWS_RANKING_INSTRUCTIONS
    return run, store


def test_news_top_ten_compared_together_and_final_order_can_change(tmp_path):
    from agent4pharma.workflows.select_local_papers import screen_local_papers

    run, store = news_run(tmp_path)
    commercial = NewsModel()
    screen_local_papers(run, store, LocalPaperModel(), lambda _: [], news_model=commercial)
    assert len(commercial.requests) == 1
    request = commercial.requests[0]
    assert len(request.papers) == 10
    # Priority journals occupy the first six places, even though other journals have higher local margins.
    assert [p.record_id for p in request.papers[:6]] == [f"r{i}" for i in range(5, -1, -1)]
    assert all(p.priority_journal for p in request.papers[:6])
    selected = sorted((i for i in run.items if i.state == "shortlisted"), key=lambda i: i.rank)
    assert [i.request.evidence[0].id for i in selected] == run.comparison_result.selected_ids
    assert len(selected) == 2 and run.phase == "fulltexts"
    assert all("全文待核实" not in i.selection_reason for i in selected)
    assert all("跨论文比较后的下载价值" in i.selection_reason for i in selected)
    assert not any(i.download_id or i.fulltext_task_id for i in run.items)
    saved = store.get(run.id)
    assert saved.comparison_request == request
    assert saved.comparison_result == run.comparison_result
    saved.items[0].selection_reason = "历史依据原文。全文待核实：旧问题。"
    historical_reason = saved.items[0].selection_reason
    screen_local_papers(saved, store, LocalPaperModel(), lambda _: [], news_model=commercial)
    assert len(commercial.requests) == 1
    assert saved.items[0].selection_reason == historical_reason


def test_news_zero_selected_completes_without_downloads(tmp_path):
    from agent4pharma.workflows.select_local_papers import screen_local_papers

    run, store = news_run(tmp_path, 2)
    screen_local_papers(run, store, LocalPaperModel(), lambda _: [], news_model=NewsModel(0))
    assert run.phase == "fulltexts"
    assert not any(i.rank or i.state == "shortlisted" for i in run.items)


def test_news_invalid_output_retries_same_pool_and_stops_after_two(tmp_path):
    from agent4pharma.workflows.select_local_papers import screen_local_papers

    run, store = news_run(tmp_path)
    commercial = NewsModel()
    commercial.invalid = True
    for _ in range(2):
        with pytest.raises(ValueError, match="推荐判断一致"):
            screen_local_papers(run, store, LocalPaperModel(), lambda _: [], news_model=commercial)
        assert run.phase == "commercial_review" and run.comparison_result is None
        assert sum(i.state == "review_failed" for i in run.items) == 10
        run = store.get(run.id)
    screen_local_papers(run, store, LocalPaperModel(), lambda _: [], news_model=commercial)
    assert len(commercial.requests) == 2
    assert commercial.requests[0] == commercial.requests[1]
    run.comparison_attempts = 0  # Explicit retry, as performed by the composition boundary.
    commercial.invalid = False
    screen_local_papers(run, store, LocalPaperModel(), lambda _: [], news_model=commercial)
    assert run.phase == "fulltexts" and len(commercial.requests) == 3


def test_news_legacy_run_is_migrated_without_changing_history(tmp_path, monkeypatch):
    from agent4pharma.composition import build_prompt_repository, open_application_storage, screen_local_batch
    from agent4pharma.infrastructure.persistence.sqlite.batch_intelligence import BatchIntelligenceRepository
    from agent4pharma.modules.discovery.news_selection import NEWS_SELECTION_VERSION
    from agent4pharma.modules.discovery.paper_selection import PaperSelectionDetails
    from agent4pharma.modules.model_configuration.prompt_contracts import PromptDraft, PromptPublication
    from agent4pharma.runtime.settings import ApplicationSettings, LocalTriageSettings

    open_application_storage(tmp_path).close()
    old, store = local_run(tmp_path, 2)
    old.news_selection_version = "paper-news-v1"
    store.save(old)
    history = {p: p.read_bytes() for p in tmp_path.rglob("*.json.gz")}
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        "agent4pharma.composition.build_intelligence_repository",
        lambda _: SimpleNamespace(
            recover_parent=lambda _: None,
        ),
    )
    monkeypatch.setattr(
        BatchIntelligenceRepository,
        "selection_details",
        lambda *_: {
            i.request.evidence[0].id: PaperSelectionDetails(source_id=i.source_id, journal=i.journal)
            for i in old.items
        },
    )
    config = ApplicationSettings(
        data_dir=tmp_path,
        paper_shortlist_limit=9,
        triage=LocalTriageSettings(
            enabled=True,
            model_revision="fixed",
            policy_json=old.local_policy.model_dump_json(),
        ),
    )
    with pytest.raises(ValueError, match="旧运行只供回放"):
        screen_local_batch(config, old.batch_id, run_id=old.id, abstract_budget=0, review_budget=0)
    new = screen_local_batch(config, old.batch_id, abstract_budget=0, review_budget=0)
    assert new.id != old.id and new.news_selection_version == NEWS_SELECTION_VERSION
    assert new.shortlist_limit == 5
    assert new.reused_abstracts_from == old.id and not new.reused_decisions_from
    assert all(i.abstract_evidence and i.local_margin is None for i in new.items)
    assert all(p.read_bytes() == content for p, content in history.items())
    assert store.get(old.id) == old
    resumed = screen_local_batch(config, old.batch_id, abstract_budget=0, review_budget=0)
    assert resumed.id == new.id
    prompts = build_prompt_repository(tmp_path)
    current = prompts.current("audience")
    assert current.id in new.ranking_instructions and current.id in new.comparison_instructions
    from agent4pharma.modules.discovery.news_selection import NEWS_COMPARISON_OUTPUT_CONTRACT

    assert new.comparison_instructions.endswith(NEWS_COMPARISON_OUTPUT_CONTRACT)
    assert current.id not in new.local_policy.instructions
    draft = prompts.draft("audience", PromptDraft(instructions=current.instructions + "\n新版本阅读需求。"))
    prompts.publish("audience", PromptPublication(version_id=draft.id, expected_current=current.id))
    frozen = screen_local_batch(config, old.batch_id, run_id=new.id, abstract_budget=0, review_budget=0)
    assert frozen.id == new.id and frozen.comparison_instructions == new.comparison_instructions
    fresh = screen_local_batch(config, old.batch_id, abstract_budget=0, review_budget=0)
    assert fresh.id != new.id
    assert draft.id in fresh.ranking_instructions and draft.id in fresh.comparison_instructions
    assert store.get(new.id).comparison_instructions == new.comparison_instructions

    for key, field in [
        ("news_ranking", "ranking_instructions"),
        ("news_comparison", "comparison_instructions"),
    ]:
        before = prompts.current(key)
        pending = prompts.draft(
            key,
            PromptDraft(
                instructions=before.instructions + "\n仅用于新版运行的具体判断。",
                examples="已发布示例：按真实输入比较。",
            ),
        )
        unchanged = screen_local_batch(config, old.batch_id, abstract_budget=0, review_budget=0)
        assert unchanged.id == fresh.id  # Saving a draft must not affect execution.
        prompts.publish(key, PromptPublication(version_id=pending.id, expected_current=before.id))
        frozen = screen_local_batch(config, old.batch_id, run_id=fresh.id, abstract_budget=0, review_budget=0)
        assert getattr(frozen, field) == getattr(fresh, field)
        updated = screen_local_batch(config, old.batch_id, abstract_budget=0, review_budget=0)
        assert updated.id != fresh.id
        assert pending.id in getattr(updated, field) and pending.content_hash in getattr(updated, field)
        assert pending.examples in getattr(updated, field)
        assert store.get(fresh.id).ranking_instructions == fresh.ranking_instructions
        assert store.get(fresh.id).comparison_instructions == fresh.comparison_instructions
        fresh = updated

    class CapturingLocal(LocalPaperModel):
        def rank(self, inputs, instructions):
            assert instructions == fresh.ranking_instructions
            return super().rank(inputs, instructions)

    local, commercial = CapturingLocal(), NewsModel(2)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-placeholder")
    monkeypatch.setattr("agent4pharma.infrastructure.llm.local_decider.LocalDecider", lambda *args: local)
    monkeypatch.setattr(
        "agent4pharma.infrastructure.llm.news_comparison.CommercialNewsComparison", lambda *args: commercial
    )
    completed = screen_local_batch(config, old.batch_id, run_id=fresh.id)
    assert completed.phase == "fulltexts"
    assert len(local.ranked) == 2 and len(commercial.requests) == 1
    assert commercial.requests[0].instructions == fresh.comparison_instructions
    assert commercial.requests[0].instructions.endswith(NEWS_COMPARISON_OUTPUT_CONTRACT)


def test_news_without_abstract_uses_title_and_explicit_affiliation(tmp_path):
    from agent4pharma.workflows.select_local_papers import screen_local_papers

    run, store = news_run(tmp_path, 1)
    item = run.items[0]
    item.affiliations = ["Verified Company Lab"]
    item.abstract_evidence.text = ""
    item.abstract_evidence.status = "unavailable"
    item.abstract_evidence.abstract_status = "none"
    local, commercial = LocalPaperModel(), NewsModel(1)
    screen_local_papers(run, store, local, lambda _: [], news_model=commercial)
    assert local.classified[0].information_status == "title_only"
    assert local.ranked[0].fields[0].field == "affiliation"
    assert commercial.requests[0].papers[0].affiliations == ["Verified Company Lab"]
    assert commercial.requests[0].papers[0].title == "AI biology"
    assert item.state == "shortlisted"


def test_news_institution_priority_precedes_journal_and_unknown_uses_news_value(tmp_path):
    from agent4pharma.workflows.select_local_papers import screen_local_papers

    run, store = news_run(tmp_path, 20)
    run.items[-1].affiliations = ["Google DeepMind, London"]
    run.items[-1].journal = ""
    run.items[-1].source_id = "arxiv"
    run.items[-2].journal = ""
    run.items[-2].source_id = "arxiv"
    commercial = NewsModel(0)
    screen_local_papers(run, store, LocalPaperModel(), lambda _: [], news_model=commercial)
    papers = commercial.requests[0].papers
    assert papers[0].record_id == "r19"
    assert papers[0].priority_institutions == ["Google DeepMind"]
    assert all(p.priority_journal for p in papers[1:7])
    # No preference between ordinary journals and preprints: news margin now decides.
    assert papers[7].record_id == "r18"


def test_news_final_ranking_enforces_institution_then_journal_then_model_order(tmp_path):
    from agent4pharma.workflows.select_local_papers import screen_local_papers

    run, store = news_run(tmp_path, 3)
    run.items[0].journal = "Other Journal"
    run.items[2].journal = "Other Journal"
    run.items[2].affiliations = ["Google DeepMind"]
    screen_local_papers(run, store, LocalPaperModel(), lambda _: [], news_model=NewsModel(3))
    assert run.comparison_result.selected_ids == ["r0", "r1", "r2"]
    ranked = sorted(run.items, key=lambda i: i.rank)
    assert [i.request.evidence[0].id for i in ranked] == ["r2", "r1", "r0"]


def test_priority_institutions_only_match_explicit_affiliations_with_word_boundaries():
    from agent4pharma.modules.discovery.news_preferences import (
        default_institution_preferences,
        match_priority_institutions,
    )

    preferences = default_institution_preferences()
    assert not match_priority_institutions(["Smith Cancer Hospital"], preferences)
    assert match_priority_institutions(["Biology, MIT, USA"], preferences) == ["MIT"]
    assert not match_priority_institutions([], preferences)


def test_commercial_value_judgment_can_exceed_download_capacity_without_extra_downloads(tmp_path):
    from agent4pharma.workflows.select_local_papers import screen_local_papers

    class MoreValuableThanCapacity(NewsModel):
        def compare(self, request):
            result = super().compare(request)
            for card in result.cards:
                card.verdict = "recommend"
            return result

    run, store = news_run(tmp_path, 3)
    screen_local_papers(run, store, LocalPaperModel(), lambda _: [], news_model=MoreValuableThanCapacity(2))
    assert sum(i.state == "shortlisted" for i in run.items) == 2
    deferred = [i for i in run.items if i.state == "deferred"]
    assert len(deferred) == 1 and "容量延期" in deferred[0].selection_reason
    assert sum(c.verdict == "recommend" for c in run.comparison_result.cards) == 3
    assert not any(i.download_id for i in run.items)


def test_selection_view_preserves_local_facts_after_fulltext(tmp_path):
    from agent4pharma.modules.discovery.paper_selection_view import selection_view
    from agent4pharma.modules.discovery.triage_contracts import DecisionProbabilities, ModelDecision

    run, _ = selection(tmp_path, 2)
    run.items[0].local_decision = ModelDecision(
        choice="analyze", probabilities=DecisionProbabilities(analyze=0.8, exclude=0.2), input_tokens=10
    )
    run.items[0].local_margin = -0.5
    run.items[0].local_rank = 1
    run.items[0].state = "complete"
    run.items[0].reason = "后续结果"
    view = selection_view(run)
    item = next(i for i in view["items"] if i["record_id"] == "r0")
    assert item["local_route"] == "analyze"
    assert item["local_margin"] == -0.5
    assert item["local_rank"] == 1
    assert view["review_pool_limit"] == 10
    assert view["authority_journals"] == run.authority_journals
    assert view["comparison_record_ids"] is None


def test_paused_journals_skip_new_screening_across_sources_without_rewriting_history(tmp_path):
    from agent4pharma.modules.discovery.paper_selection import PaperSelectionDetails, create_selection_run
    from agent4pharma.modules.discovery.paper_selection_view import selection_view
    from agent4pharma.runtime.settings import ApplicationSettings

    original, store = selection(tmp_path, 3)
    original.items[0].source_id, original.items[0].journal = "pubmed", "NEJM AI"
    original.items[1].source_id, original.items[1].journal = "rss", " nejm   ai "
    store.save(original)
    before = store.get(original.id).model_dump_json()
    requests = [item.request for item in original.items]
    details = {
        item.request.evidence[0].id: PaperSelectionDetails(
            source_id=item.source_id,
            journal=item.journal,
        )
        for item in original.items
    }
    run = create_selection_run(
        "batch", requests, details, original.created_at, 5, paused_journals=["NEJM AI"]
    )
    assert len(run.items) == 1
    assert {item.source_id for item in run.paused_items} == {"pubmed", "rss"}
    assert all(item.local_decision is None and item.local_rank is None for item in run.paused_items)
    assert "NEJM AI" not in run.authority_journals
    view = selection_view(run)
    assert view["paused_journals"] == ["NEJM AI"]
    assert len(view["paused_papers"]) == 2
    assert view["progress"]["total"] == 1
    assert store.get(original.id).model_dump_json() == before
    restored = create_selection_run(
        "batch",
        [i.request for i in [*run.items, *run.paused_items]],
        details,
        original.created_at,
        5,
        paused_journals=[],
    )
    assert len(restored.items) == 3 and not restored.paused_items
    assert restored.id != run.id
    assert "paused_journals" not in ApplicationSettings().snapshot()
    assert ApplicationSettings(paused_journals=["NEJM AI"]).snapshot()["paused_journals"] == ["NEJM AI"]


def test_retired_per_paper_review_cannot_execute_even_with_injected_model(tmp_path):
    from agent4pharma.workflows.select_local_papers import screen_local_papers

    run, store = local_run(tmp_path, 2)
    local = LocalPaperModel()
    with pytest.raises(ValueError):
        screen_local_papers(run, store, local, lambda _: pytest.fail("retired input must not read sources"))
    assert not local.classified and not local.ranked


def test_news_ranking_retry_keeps_classification_and_never_uses_per_paper_review(tmp_path):
    from agent4pharma.workflows.select_local_papers import screen_local_papers

    run, store = news_run(tmp_path, 4)
    local, commercial = LocalPaperModel(), NewsModel()
    local.fail_rank = True
    with pytest.raises(RuntimeError):
        screen_local_papers(run, store, local, lambda _: [], news_model=commercial)
    assert not commercial.requests
    assert all(item.state == "local_failed" for item in run.items)
    local.fail_rank = False
    screen_local_papers(run, store, local, lambda _: [], news_model=commercial)
    assert len(local.classified) == 4
    assert len(commercial.requests) == 1


def test_news_missing_abstract_uses_source_excerpt_in_current_comparison(tmp_path):
    from agent4pharma.workflows.select_local_papers import screen_local_papers

    run, store = news_run(tmp_path, 1)
    item = run.items[0]
    item.abstract_evidence.text = ""
    item.abstract_evidence.status = "unavailable"
    item.abstract_evidence.abstract_status = "none"
    item.request.evidence[0].text = "AI biology source excerpt with concrete model findings"
    local, commercial = LocalPaperModel(), NewsModel(1)
    screen_local_papers(run, store, local, lambda _: [], news_model=commercial)
    assert local.classified[0].information_status == "source_text"
    assert commercial.requests[0].papers[0].text == item.request.evidence[0].text


def test_news_abstract_fetch_error_remains_failure_and_never_reaches_comparison(tmp_path):
    from agent4pharma.workflows.select_local_papers import screen_local_papers

    run, store = news_run(tmp_path, 1)
    run.items[0].abstract_evidence = None
    local, commercial = LocalPaperModel(), NewsModel()
    with pytest.raises(RuntimeError):
        screen_local_papers(
            run,
            store,
            local,
            lambda _: [
                IntelligenceEvidence(id="r0", text="", status="access_failed", abstract_status="error")
            ],
            news_model=commercial,
        )
    assert run.items[0].state == "local_failed"
    assert not local.classified and not commercial.requests


@pytest.mark.parametrize("native_abstract", [False, True])
def test_local_input_bounds_long_text_and_keeps_original_evidence(tmp_path, native_abstract):
    from agent4pharma.workflows.select_local_papers import _input

    run, _ = news_run(tmp_path, 1)
    item = run.items[0]
    text = "AI biology evidence. " * 2000
    item.request.evidence[0].text = text
    item.abstract_evidence.text = text if native_abstract else ""
    item.abstract_evidence.status = "available" if native_abstract else "unavailable"
    first = _input(item, run)
    assert first.fields[0].field == ("abstract" if native_abstract else "source_text")
    assert first.fields[0].text == text[:12000] and first.fields[0].truncated
    assert item.request.evidence[0].text == text
    assert item.abstract_evidence.text == (text if native_abstract else "")
    assert first.extraction_version == "local-abstract-v5"
