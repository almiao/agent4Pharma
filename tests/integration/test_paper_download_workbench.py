import hashlib
import json
import re
import threading
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from agent4pharma.composition import build_maintenance_workbench, build_paper_download_workbench
from agent4pharma.entrypoints.web.paper_download_api import PaperDownloadAPI
from agent4pharma.entrypoints.web.server import WorkbenchHTTPServer
from agent4pharma.infrastructure.persistence.chaoslib_access import ChaosLibAccess
from agent4pharma.infrastructure.persistence.paper_download_job_repository import PaperDownloadJobRepository
from agent4pharma.infrastructure.persistence.paper_download_repository import PaperDownloadRepository
from agent4pharma.infrastructure.persistence.paper_selection_repository import PaperSelectionRepository
from agent4pharma.infrastructure.web.daily_browser_connection import DailyBrowserConnection
from agent4pharma.infrastructure.web.daily_browser_downloader import DailyBrowserDownloader
from agent4pharma.modules.discovery.intelligence_contracts import (
    IntelligenceEvidence,
    IntelligencePolicy,
    IntelligencePolicyVersion,
    IntelligenceRequest,
)
from agent4pharma.modules.discovery.paper_selection import PaperSelectionItem, PaperSelectionRun
from agent4pharma.modules.discovery.paper_selection_view import selection_view
from agent4pharma.modules.materials.paper_download_jobs import PaperBrowserStatus, StartPaperDownload
from agent4pharma.runtime.settings import ApplicationSettings


@pytest.fixture
def selection(tmp_path, monkeypatch):
    from agent4pharma.infrastructure.persistence import manual_pdf_inbox
    from agent4pharma.infrastructure.persistence.manual_pdf_directories import DownloadLocations

    monkeypatch.setattr(manual_pdf_inbox, "discover_download_locations", lambda: DownloadLocations(()))
    policy = IntelligencePolicyVersion(
        id="policy",
        version=1,
        status="published",
        content_hash="hash",
        definition=IntelligencePolicy(instructions="Offline screening policy " * 3),
    )
    run = PaperSelectionRun(
        id="selection-" + "c" * 24,
        batch_id="batch",
        phase="fulltexts",
        created_at="2026-11-01T00:00:00Z",
        shortlist_limit=1,
        items=[
            PaperSelectionItem(
                rank=i + 1,
                state="shortlisted" if i == 0 else "deferred",
                request=IntelligenceRequest(
                    material_ref=f"m{i}",
                    model="offline",
                    policy=policy,
                    evidence=[
                        IntelligenceEvidence(
                            id=f"r{i}",
                            title="Protein foundation model for biological discovery",
                            text="Offline fixture",
                            doi=f"10.1038/paper{i}",
                        )
                    ],
                ),
            )
            for i in range(2)
        ],
    )
    PaperSelectionRepository(tmp_path).save(run)
    return run


def values(run, **kwargs):
    return StartPaperDownload(run_id=run.id, record_id="r0", **kwargs)


def test_scope_and_no_get_side_effects(tmp_path, selection):
    app = build_paper_download_workbench(tmp_path)
    assert app.status(selection.id, "r0").phase == "not_started"
    assert not (tmp_path / "private/chaoslib/access.json").exists()
    with pytest.raises(ValueError, match="个位数"):
        app.start(StartPaperDownload(run_id=selection.id, record_id="r1"))
    selection.phase = "abstracts"
    PaperSelectionRepository(tmp_path).save(selection)
    with pytest.raises(ValueError, match="个位数"):
        app.start(values(selection))
    assert not any(i["can_download"] for i in selection_view(selection)["items"])
    with pytest.raises(ValueError):
        PaperDownloadJobRepository(tmp_path).get("../../private")


def test_web_download_requires_explicit_ready_connection_before_creating_job(tmp_path, selection):
    api = PaperDownloadAPI(build_paper_download_workbench(tmp_path))
    try:
        assert api.connection_status()["phase"] == "disconnected"
        with pytest.raises(ValueError, match="先连接"):
            api.post(values(selection).model_dump())
        assert api.app.store.all() == []
        assert not (tmp_path / "private/chaoslib/access.json").exists()
    finally:
        api.close()


def test_connect_requests_are_merged_and_never_consume_paper_budget(tmp_path, selection):
    started, release = threading.Event(), threading.Event()

    class Browser:
        calls = 0
        phase = "disconnected"
        closed = False

        def status(self):
            return PaperBrowserStatus(phase=self.phase)

        def connect(self):
            self.calls += 1
            self.phase = "connecting"
            started.set()
            assert release.wait(5)
            self.phase = "ready"
            return self.status()

        def close(self):
            self.closed = True

    app = build_paper_download_workbench(tmp_path)
    app.browser = browser = Browser()
    api = PaperDownloadAPI(app)
    try:
        with pytest.raises(ValueError, match="不接受"):
            api.connect({"url": "https://unbound.example"})
        api.connect({})
        assert started.wait(3)
        api.connect({})
        assert api.connection_status()["phase"] == "connecting" and browser.calls == 1
        with pytest.raises(ValueError, match="正在连接"):
            api.post(values(selection).model_dump())
        release.set()
        for _ in range(100):
            if api.connection_status()["phase"] == "ready":
                break
            time.sleep(0.01)
        assert api.connection_status()["phase"] == "ready"
        assert not (tmp_path / "private/chaoslib/access.json").exists()
    finally:
        release.set()
        api.close()
    assert browser.closed


def test_paused_account_requires_explicit_restore_and_preserves_budget(tmp_path, selection, monkeypatch):
    guard = ChaosLibAccess(tmp_path / "private/chaoslib")
    guard.reserve()
    guard.block("blocked_automation")
    before = guard.status()

    async def forbidden(*_):
        pytest.fail("must not connect")

    monkeypatch.setattr(DailyBrowserDownloader, "_acquire", forbidden)
    app = build_paper_download_workbench(tmp_path)
    job = app.start(values(selection))
    app.execute(job.id)
    assert app.status(selection.id, "r0").reason == "account_blocked_requires_manual"
    assert guard.status() == before
    assert app.start(values(selection)).id == job.id
    retry = app.start(values(selection, retry=True, restore_account=True))
    app.execute(retry.id)
    assert app.status(selection.id, "r0").reason == "attempt_interval"
    assert guard.status()["count"] == before["count"] and "reason" not in guard.status()


def test_interrupted_job_is_queryable_and_never_automatically_restarted(tmp_path, selection):
    app = build_paper_download_workbench(tmp_path)
    job = app.start(values(selection))
    job.expires_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    app.store.save(job)
    fresh = build_paper_download_workbench(tmp_path)
    assert fresh.status(selection.id, "r0").reason == "download_interrupted"
    assert fresh.start(values(selection)).id == job.id
    assert fresh.start(values(selection, retry=True)).id != job.id


def test_only_one_paper_can_wait_for_browser_at_a_time(tmp_path, selection):
    selection.shortlist_limit = 2
    selection.items[1].state = "shortlisted"
    PaperSelectionRepository(tmp_path).save(selection)
    app = build_paper_download_workbench(tmp_path)
    app.start(values(selection))
    with pytest.raises(ValueError, match="已有一篇"):
        app.start(StartPaperDownload(run_id=selection.id, record_id="r1"))
    assert not (tmp_path / "private/chaoslib/access.json").exists()


def test_http_download_progress_dedup_pdf_validation_and_export(tmp_path, selection, monkeypatch):
    # Real local HTTP + repositories + PDF validation. Browser/site is an offline transport fixture.
    import pymupdf

    started, release = threading.Event(), threading.Event()
    calls = []
    connect = "chrome-extension://mmlmfjhmonkocbjadbfplnigmagldckm/connect.html?local-test=1"

    async def acquire(self, code):
        assert not self._reserve_publisher_attempt()
        calls.append(1)
        self._progress("waiting_connection", connect)
        started.set()
        assert release.wait(5)
        output = json.loads(re.search(r"const task = (.*);", code).group(1))["file"]
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((50, 50), selection.items[0].request.evidence[0].title + "\n10.1038/paper0")
            document.save(output)
        return {"status": "file_saved"}

    monkeypatch.setattr(DailyBrowserDownloader, "_acquire", acquire)
    monkeypatch.setattr(DailyBrowserConnection, "status", lambda _: PaperBrowserStatus(phase="ready"))
    maintenance = build_maintenance_workbench(
        tmp_path / "config.toml", ApplicationSettings(data_dir=tmp_path)
    )
    download = PaperDownloadAPI(build_paper_download_workbench(tmp_path))
    server = WorkbenchHTTPServer(maintenance, 0, paper_downloads=download)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    params = {"run": selection.id, "record": "r0"}
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
            body = values(selection).model_dump()
            assert client.post("/api/paper-downloads", json=body).status_code == 403
            assert client.get("/api/paper-downloads/file", params=params).status_code == 403
            assert client.get("/api/paper-downloads/connection").status_code == 403
            assert client.post("/api/paper-downloads/connect", json={}).status_code == 403
            client.headers["X-Workbench-Token"] = server.token
            assert client.get("/api/paper-downloads/connection").json()["phase"] == "ready"
            assert (
                client.post(
                    "/api/paper-downloads/connect", json={"url": "https://unbound.example"}
                ).status_code
                == 400
            )
            assert (
                client.post(
                    "/api/paper-downloads", json={**body, "url": "https://unbound.example"}
                ).status_code
                == 400
            )
            first = client.post("/api/paper-downloads", json=body).json()
            assert started.wait(3)
            assert client.post("/api/paper-downloads", json=body).json()["job_id"] == first["job_id"]
            current = client.get("/api/paper-downloads", params=params).json()
            assert current["phase"] == "waiting_connection" and current["connect_url"] == connect
            jobs = list((tmp_path / "paper-downloads/jobs").glob("*.json"))
            assert len(jobs) == 1 and connect not in jobs[0].read_text(encoding="utf-8")
            release.set()
            for _ in range(100):
                current = client.get("/api/paper-downloads", params=params).json()
                if current["phase"] == "downloaded":
                    break
                time.sleep(0.02)
            assert current["phase"] == "downloaded" and not current["connect_url"]
            response = client.get("/api/paper-downloads/file", params=params)
            assert response.status_code == 200 and response.content.startswith(b"%PDF")
            result = PaperDownloadRepository(tmp_path).results()[0]
            assert hashlib.sha256(response.content).hexdigest() == result.sha256
            assert PaperSelectionRepository(tmp_path).get(selection.id).items[0].download_id == result.id
            assert len(calls) == 1
            client.post("/api/paper-downloads", json=body)
            assert len(calls) == 1
            jobs[0].unlink()  # Simulate a validated PDF acquired before HTTP jobs existed.
            assert client.get("/api/paper-downloads", params=params).json()["phase"] == "downloaded"
            from pathlib import Path

            Path(result.file).write_bytes(b"%PDF-changed")
            assert client.get("/api/paper-downloads/file", params=params).status_code == 404
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        download.close()
        maintenance.close()
        thread.join(3)


def test_manual_debug_before_shortlist_is_isolated_and_does_not_start_models(
    tmp_path, selection, monkeypatch
):
    from agent4pharma.modules.materials.paper_downloads import PaperDownloadResult

    selection.phase = "abstracts"
    for item in selection.items:
        item.rank = None
        item.state = "awaiting_abstract"
    repository = PaperSelectionRepository(tmp_path)
    repository.save(selection)
    before = repository.get(selection.id).model_dump_json()
    calls = []

    def acquire(self, request):
        calls.append(request)
        return PaperDownloadResult(
            id="debug-result",
            request=request,
            channel="daily_browser",
            status="manual_required",
            reason="publisher_verification_required",
            created_at=datetime.now(UTC).isoformat(),
        )

    monkeypatch.setattr(DailyBrowserDownloader, "download", acquire)
    app = build_paper_download_workbench(tmp_path, debug=True)
    with pytest.raises(ValueError):
        app.start(StartPaperDownload(run_id=selection.id, record_id="not-in-run"))
    job = app.start(values(selection))
    assert job.request.decision_id.startswith("debug:")
    assert app.start(values(selection)).id == job.id
    app.execute(job.id)
    assert len(calls) == 1
    assert app.status(selection.id, "r0").phase == "manual_required"
    assert repository.get(selection.id).model_dump_json() == before
    normal = build_paper_download_workbench(tmp_path)
    with pytest.raises(ValueError, match="个位数"):
        normal.start(values(selection))


def inbox_pdf(tmp_path, name, title, doi="10.1038/paper0"):
    import pymupdf

    path = tmp_path / "paper-downloads/inbox" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), title + "\n" + doi)
        document.save(path)
    return path


def test_manual_inbox_auto_matches_copies_and_is_idempotent(tmp_path, selection):
    from pathlib import Path

    from agent4pharma.composition import build_manual_pdf_imports
    from agent4pharma.modules.materials.manual_pdf_imports import ManualPdfConfirmation

    title = selection.items[0].request.evidence[0].title
    source = inbox_pdf(tmp_path, "random-browser-name.pdf", title)
    inbox_pdf(tmp_path, "newer-unrelated.pdf", "Entirely different unrelated research", "10.1007/other")
    before = source.read_bytes()
    selection.items[0].reason = "Original commercial screening conclusion"
    PaperSelectionRepository(tmp_path).save(selection)
    app = build_manual_pdf_imports(tmp_path)
    request = ManualPdfConfirmation(run_id=selection.id, record_id="r0")
    result = app.confirm(request)
    assert result.status == "imported" and result.filename == source.name
    assert source.read_bytes() == before and not source.with_suffix(".txt").exists()
    ledger = PaperDownloadRepository(tmp_path)
    stored = ledger.results()[0]
    assert Path(stored.file).parent == tmp_path / "paper-downloads/manual"
    assert Path(stored.file).read_bytes() == before
    assert Path(stored.text_file).is_file()
    assert app.confirm(request).result_id == result.result_id
    assert len(ledger.attempts()) == 1
    run = PaperSelectionRepository(tmp_path).get(selection.id)
    assert run.items[0].download_id == result.result_id
    assert run.items[0].reason == "Original commercial screening conclusion"
    assert selection_view(run)["items"][0]["pdf_imported"] is True
    assert not run.items[0].fulltext_task_id


def test_unrecommended_paper_can_be_selected_associated_read_and_confirmed(tmp_path, selection):
    from agent4pharma.entrypoints.web.api import WorkbenchAPI
    from agent4pharma.modules.discovery.processed_papers import processed_paper_keys
    from agent4pharma.workflows.imported_paper_recommendation import (
        confirm_imported_recommendation,
        imported_recommendation_candidate,
    )

    item = selection.items[1]
    source = inbox_pdf(tmp_path, "manual-choice.pdf", item.request.evidence[0].title, "10.1038/paper1")
    maintenance = build_maintenance_workbench(
        tmp_path / "config.toml", ApplicationSettings(data_dir=tmp_path)
    )
    calls = []
    api = WorkbenchAPI(maintenance, lambda *args: calls.append(args))
    body = {"run_id": selection.id, "record_id": "r1"}
    try:
        api.post("/api/paper-selections/manual", body)
        receipt = api.post("/api/manual-pdfs/confirm", body)
        assert receipt["status"] == "imported" and "followup_id" not in receipt
        assert not calls
        run = PaperSelectionRepository(tmp_path).get(selection.id)
        assert run.items[1].manual_selected_at
        assert run.items[1].rank == 2 and run.shortlist_limit == 1
        assert run.items[1].human_recommendation is None
        downloads = build_paper_download_workbench(tmp_path)
        assert downloads.ready_pdf(run.id, "r1") == source.read_bytes()
        with pytest.raises(ValueError, match="个位数"):
            downloads.start(StartPaperDownload(run_id=run.id, record_id="r1"))
        result = PaperDownloadRepository(tmp_path).results()[0]
        assert processed_paper_keys([result])
        confirm_imported_recommendation(run, "r1", result)
        assert imported_recommendation_candidate(run, "r1", result).evidence[0].document
    finally:
        maintenance.close()


def test_manual_inbox_ambiguity_unknown_and_partial_files_do_not_autopick(tmp_path, selection):
    from agent4pharma.composition import build_manual_pdf_imports
    from agent4pharma.modules.materials.manual_pdf_imports import ManualPdfConfirmation

    app = build_manual_pdf_imports(tmp_path)
    request = ManualPdfConfirmation(run_id=selection.id, record_id="r0")
    assert app.confirm(request).status == "not_found"
    first = inbox_pdf(tmp_path, "version-a.pdf", selection.items[0].request.evidence[0].title)
    inbox_pdf(tmp_path, "version-b.pdf", selection.items[0].request.evidence[0].title)
    (first.parent / "partial.pdf.crdownload").write_bytes(b"%PDF incomplete")
    result = app.confirm(request)
    assert result.status == "choose_file" and len(result.candidates) == 2
    assert not PaperDownloadRepository(tmp_path).attempts()
    chosen = next(file for file in result.candidates if file.filename == first.name)
    imported = app.confirm(request.model_copy(update={"file_id": chosen.file_id}))
    assert imported.status == "imported" and imported.filename == first.name
    with pytest.raises(ValueError):
        app.confirm(request.model_copy(update={"record_id": "not-registered"}))


def test_manual_inbox_http_is_token_protected_and_rejects_paths(tmp_path, selection):
    title = selection.items[0].request.evidence[0].title
    inbox_pdf(tmp_path, "paper.pdf", title)
    maintenance = build_maintenance_workbench(
        tmp_path / "config.toml", ApplicationSettings(data_dir=tmp_path)
    )
    server = WorkbenchHTTPServer(maintenance, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
            body = {"run_id": selection.id, "record_id": "r0"}
            assert client.post("/api/manual-pdfs/confirm", json=body).status_code == 403
            client.headers["X-Workbench-Token"] = server.token
            assert (
                client.post("/api/manual-pdfs/confirm", json={**body, "path": "C:/arbitrary.pdf"}).status_code
                == 400
            )
            response = client.post("/api/manual-pdfs/confirm", json=body)
            assert response.status_code == 200 and response.json()["status"] == "imported"
            assert "sha256" not in response.json()
    finally:
        server.shutdown()
        server.server_close()
        maintenance.close()
        thread.join(3)


def test_import_success_calls_followup_hook_but_missing_file_does_not(tmp_path, selection):
    from agent4pharma.entrypoints.web.api import WorkbenchAPI

    maintenance = build_maintenance_workbench(
        tmp_path / "config.toml", ApplicationSettings(data_dir=tmp_path)
    )
    submitted = []

    def submit(run_id, record_id):
        submitted.append((run_id, record_id))
        return "followup"

    try:
        api = WorkbenchAPI(maintenance, submit)
        body = {"run_id": selection.id, "record_id": "r0", "recommend_and_start": True}
        assert api.post("/api/manual-pdfs/confirm", body)["status"] == "not_found"
        assert not submitted
        inbox_pdf(tmp_path, "paper.pdf", selection.items[0].request.evidence[0].title)
        result = api.post("/api/manual-pdfs/confirm", body)
        assert result["status"] == "imported" and result["followup_id"] == "followup"
        assert submitted == [(selection.id, "r0")]
    finally:
        maintenance.close()


def test_import_validation_reads_identity_page_without_prefetching_body(tmp_path):
    from pathlib import Path

    import pymupdf

    from agent4pharma.infrastructure.web.paper_pdf_validation import validate_pdf

    source = tmp_path / "main.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text(
            (30, 60), "Protein foundation model for biological discovery 10.1038/paper0"
        )
        pdf.new_page().insert_text((30, 60), "UNREAD_SECOND_PAGE_SENTINEL")
        pdf.save(source)
    result = validate_pdf(source, "10.1038/paper0", "Protein foundation model for biological discovery")
    assert result["status"] == "downloaded" and result["pages"] == 2
    assert result["text_scope"] == "identity_first_page"
    assert "UNREAD_SECOND_PAGE_SENTINEL" not in Path(result["text_file"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("pending_cache", [False, True])
def test_human_import_goes_directly_to_production_without_fulltext_screening(
    tmp_path, selection, monkeypatch, pending_cache
):
    from agent4pharma import composition
    from agent4pharma.entrypoints.web.api import WorkbenchAPI
    from agent4pharma.workflows.production_dependencies import ProductionContext

    inbox_pdf(tmp_path, "confirmed.pdf", selection.items[0].request.evidence[0].title)
    config = ApplicationSettings(data_dir=tmp_path)
    maintenance = build_maintenance_workbench(tmp_path / "config.toml", config)
    executions = []

    def execute(root, task_id):
        app = composition.build_production_workbench(root)
        task = app.execution.get(task_id)
        executions.append(task)
        if pending_cache:
            from agent4pharma.runtime.production_execution import ProductionPreparationPending

            raise ProductionPreparationPending()
        return task.model_copy(update={"status": "failed", "error": "已留存的角色失败说明"})

    assert not hasattr(composition, "build_intelligence_service")
    monkeypatch.setattr(composition, "execute_production_task", execute)
    continuation = composition.build_paper_continuation(config)
    api = WorkbenchAPI(maintenance, continuation.confirm_import)
    body = {"run_id": selection.id, "record_id": "r0"}
    try:
        imported = api.post("/api/manual-pdfs/confirm", body)
        assert "followup_id" not in imported
        assert PaperSelectionRepository(tmp_path).get(selection.id).items[0].human_recommendation is None
        receipt = api.post("/api/manual-pdfs/confirm", {**body, "recommend_and_start": True})
        continuation.executor.shutdown(wait=True)
        assert receipt["followup_id"] == selection.id + ":r0"
        assert len(executions) == 1
        context = ProductionContext.model_validate_json(executions[0].input_json)
        assert context.candidates[0].assessment_id == ""
        assert context.candidates[0].evidence[0].document is not None
        run = PaperSelectionRepository(tmp_path).get(selection.id)
        assert run.items[0].human_recommendation is not None
        assert run.items[0].fulltext_task_id == ""
        assert run.items[0].continuation_status == "failed"
        assert run.items[0].continuation_task_id == executions[0].id
        assert run.items[0].continuation_error == (
            "PDF解析缓存尚未就绪，当前任务尚未调用模型；请查看文档准备状态，准备完成后显式执行。"
            if pending_cache
            else "已留存的角色失败说明"
        )
        # 同一确认不重复启动；下游失败保留在制作链，不倒改为推荐否决。
        api.post("/api/manual-pdfs/confirm", {**body, "recommend_and_start": True})
        assert len(executions) == 1
    finally:
        continuation.close()
        maintenance.close()


def test_manual_import_searches_browser_and_system_downloads_without_moving_sources(
    tmp_path, selection, monkeypatch
):
    from agent4pharma.composition import build_manual_pdf_imports
    from agent4pharma.infrastructure.persistence import manual_pdf_inbox
    from agent4pharma.infrastructure.persistence.manual_pdf_directories import DownloadLocations
    from agent4pharma.modules.materials.manual_pdf_imports import ManualPdfConfirmation

    browser, system = tmp_path / "ChromeDownloads", tmp_path / "Downloads"
    browser.mkdir()
    system.mkdir()
    original = inbox_pdf(tmp_path, "shared.pdf", selection.items[0].request.evidence[0].title)
    contents = original.read_bytes()
    original.unlink()
    (browser / "shared.pdf").write_bytes(contents)
    (system / "shared.pdf").write_bytes(contents)
    monkeypatch.setattr(
        manual_pdf_inbox, "discover_download_locations", lambda: DownloadLocations((browser, system))
    )
    app = build_manual_pdf_imports(tmp_path)
    request = ManualPdfConfirmation(run_id=selection.id, record_id="r0")
    candidates = app.confirm(request)
    assert candidates.status == "choose_file"
    assert len(candidates.directories) == 3
    assert {c.directory for c in candidates.candidates} == {str(browser), str(system)}
    assert len({c.file_id for c in candidates.candidates}) == 2
    selected = next(c for c in candidates.candidates if c.directory == str(browser))
    imported = app.confirm(request.model_copy(update={"file_id": selected.file_id}))
    assert imported.status == "imported"
    assert (browser / "shared.pdf").read_bytes() == contents
    assert (system / "shared.pdf").read_bytes() == contents
    run = PaperSelectionRepository(tmp_path).get(selection.id)
    assert run.items[0].human_recommendation is None
    assert run.items[0].fulltext_task_id == ""


def test_paused_journal_rejects_new_recommendation_from_an_old_shortlist(tmp_path, selection, monkeypatch):
    from agent4pharma import composition
    from agent4pharma.entrypoints.web.api import WorkbenchAPI

    selection.items[0].journal = "NEJM AI"
    PaperSelectionRepository(tmp_path).save(selection)
    inbox_pdf(tmp_path, "nejm.pdf", selection.items[0].request.evidence[0].title)
    config = ApplicationSettings(data_dir=tmp_path, paused_journals=["NEJM AI"])
    maintenance = build_maintenance_workbench(tmp_path / "config.toml", config)
    calls = []
    monkeypatch.setattr(composition, "execute_production_task", lambda *args: calls.append(args))
    continuation = composition.build_paper_continuation(config)
    try:
        api = WorkbenchAPI(maintenance, continuation.confirm_import)
        with pytest.raises(ValueError, match="期刊已暂停"):
            api.post(
                "/api/manual-pdfs/confirm",
                {
                    "run_id": selection.id,
                    "record_id": "r0",
                    "recommend_and_start": True,
                },
            )
        continuation.executor.shutdown(wait=True)
        assert not calls
        run = PaperSelectionRepository(tmp_path).get(selection.id)
        assert run.items[0].human_recommendation is None
        assert run.items[0].continuation_status == "idle"
    finally:
        continuation.close()
        maintenance.close()
