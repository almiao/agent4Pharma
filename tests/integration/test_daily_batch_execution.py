"""今日批次的授权、幂等、真实状态与HTTP边界；默认不调用网络来源或模型。"""

from concurrent.futures import Executor, Future, ThreadPoolExecutor
from datetime import date
from threading import Thread
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from agent4pharma.composition import build_maintenance_workbench
from agent4pharma.entrypoints.web.server import WorkbenchHTTPServer
from agent4pharma.infrastructure.persistence.daily_batch_run_repository import DailyBatchRunRepository
from agent4pharma.modules.discovery.daily_batch_runs import (
    DailyBatchResult,
    RetryPaperSelection,
    StartDailyBatch,
)
from agent4pharma.runtime.settings import ApplicationSettings
from agent4pharma.workflows.daily_batch_execution import DailyBatchExecution

DAY = date(2026, 11, 5)


class DeferredExecutor(Executor):
    def __init__(self):
        self.calls = []

    def submit(self, function, *args, **kwargs):
        self.calls.append((function, args, kwargs))
        return Future()

    def finish(self):
        function, args, kwargs = self.calls.pop(0)
        function(*args, **kwargs)


def result(partial=False):
    return DailyBatchResult(
        batch_id="daily-today",
        selection_run_id="selection-today",
        materials=0,
        suggested_downloads=0,
        coverage="partial" if partial else "complete",
        partial=partial,
    )


def test_today_request_cannot_expand_scope_and_read_does_not_execute(tmp_path):
    executor = DeferredExecutor()
    app = DailyBatchExecution(
        DailyBatchRunRepository(tmp_path),
        lambda: DAY,
        lambda *_: pytest.fail("read must not execute"),
        executor,
    )
    assert app.snapshot().day == DAY and app.snapshot().job is None
    with pytest.raises(ValueError, match="业务日"):
        app.start(StartDailyBatch(day=date(2026, 11, 4), request_id=uuid4()))
    with pytest.raises(ValidationError):
        StartDailyBatch(day=DAY, request_id=uuid4(), command="download-and-publish")
    assert not executor.calls and not app.runs.all()


def test_concurrent_clicks_reuse_active_job_and_same_request_reuses_completed_job(tmp_path):
    executor = DeferredExecutor()
    states = []
    app = None

    def operation(day, progress):
        assert day == DAY
        for status in ["collecting", "selecting"]:
            progress(status, "daily-today")
            states.append(app.snapshot().job.status)
        return result()

    app = DailyBatchExecution(DailyBatchRunRepository(tmp_path), lambda: DAY, operation, executor)
    requests = [StartDailyBatch(day=DAY, request_id=uuid4()) for _ in range(12)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(app.start, requests))
    assert len({job.id for job in jobs}) == 1 and len(executor.calls) == 1
    assert app.snapshot().job.status == "queued"
    executor.finish()
    assert states == ["collecting", "selecting"]
    assert app.snapshot().job.status == "succeeded"  # 零篇也可以是完整覆盖，不制造推荐。
    assert {app.start(request).id for request in requests} == {jobs[0].id}
    assert not executor.calls
    app.start(StartDailyBatch(day=DAY, request_id=uuid4()))
    assert len(executor.calls) == 1


def test_partial_failure_and_restart_keep_real_outcomes_without_automatic_retry(tmp_path):
    executor = DeferredExecutor()
    repository = DailyBatchRunRepository(tmp_path)
    app = DailyBatchExecution(repository, lambda: DAY, lambda *_: result(partial=True), executor)
    app.start(StartDailyBatch(day=DAY, request_id=uuid4()))
    executor.finish()
    assert app.snapshot().job.status == "partial"

    def fail(day, progress):
        progress("selecting", "daily-saved")
        raise RuntimeError("private-token-must-not-be-persisted")

    app.operation = fail
    app.start(StartDailyBatch(day=DAY, request_id=uuid4()))
    executor.finish()
    assert app.snapshot().job.status == "failed"
    assert app.snapshot().job.batch_id == "daily-saved"
    assert "private-token" not in app.snapshot().job.error
    request = StartDailyBatch(day=DAY, request_id=uuid4())
    queued = app.start(request)
    restarted = DailyBatchExecution(repository, lambda: DAY, fail, DeferredExecutor())
    restarted.recover_interrupted()
    assert restarted.start(request).status == "failed"
    assert restarted.snapshot().job.id == queued.id
    assert len(restarted.executor.calls) == 0


def test_http_requires_same_origin_token_and_post_to_execute(tmp_path):
    config = ApplicationSettings(data_dir=tmp_path, sources={"biorxiv": {"query": ""}})
    maintenance = build_maintenance_workbench(tmp_path / "config.toml", config)
    executor = DeferredExecutor()
    batch = DailyBatchExecution(
        DailyBatchRunRepository(tmp_path),
        lambda: DAY,
        lambda *_: result(),
        executor,
        retry_operation=lambda *_: result(),
    )
    server = WorkbenchHTTPServer(maintenance, 0, daily_batch=batch)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
            assert client.get("/api/daily-batch").status_code == 403
            headers = {"X-Workbench-Token": server.token}
            assert client.get("/api/daily-batch", headers=headers).json()["job"] is None
            body = {"day": DAY.isoformat(), "request_id": str(uuid4())}
            assert client.post("/api/daily-batch", json=body).status_code == 403
            assert (
                client.post(
                    "/api/daily-batch", json=body, headers=headers | {"Origin": "https://other.example"}
                ).status_code
                == 403
            )
            assert not executor.calls
            assert (
                client.post(
                    "/api/daily-batch", json=body | {"allow_download": True}, headers=headers
                ).status_code
                == 400
            )
            first = client.post("/api/daily-batch", json=body, headers=headers)
            assert first.status_code == 200
            second = client.post("/api/daily-batch", json=body, headers=headers)
            assert second.json()["id"] == first.json()["id"] and len(executor.calls) == 1
            activity = client.get("/api/background-activity", headers=headers).json()
            assert activity["counts"]["queued"] == 1
            assert [item["kind"] for item in activity["items"]] == ["日常跑批"]
            executor.finish()
            activity = client.get("/api/background-activity", headers=headers).json()
            assert activity["counts"]["queued"] == 0 and activity["counts"]["complete"] == 1
            retry_body = {"request_id": str(uuid4()), "selection_run_id": "selection-" + "a" * 24}
            path = "/api/daily-batch/retry-selection"
            assert client.post(path, json=retry_body).status_code == 403
            assert client.post(path, json=retry_body | {"papers": []}, headers=headers).status_code == 400
            retry = client.post(path, json=retry_body, headers=headers)
            assert (
                retry.status_code == 200
                and retry.json()["selection_run_id"] == retry_body["selection_run_id"]
            )
            assert client.post(path, json=retry_body, headers=headers).json()["id"] == retry.json()["id"]
            assert len(executor.calls) == 1
            executor.finish()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        maintenance.close()


def test_selection_retry_is_exact_scoped_and_does_not_execute_daily_collection(tmp_path):
    executor = DeferredExecutor()
    seen = []
    identity = "selection-" + "c" * 24

    def retry(run_id, progress):
        seen.append(run_id)
        progress("selecting", "daily-existing")
        return result(partial=True)

    app = DailyBatchExecution(
        DailyBatchRunRepository(tmp_path),
        lambda: DAY,
        lambda *_: pytest.fail("selection retry must not collect"),
        executor,
        retry_operation=retry,
    )
    request = RetryPaperSelection(request_id=uuid4(), selection_run_id=identity)
    job = app.retry_selection(request)
    assert job.selection_run_id == identity and not seen
    assert app.retry_selection(request).id == job.id and len(executor.calls) == 1
    with pytest.raises(ValueError, match="另一个批次操作"):
        app.start(StartDailyBatch(day=DAY, request_id=uuid4()))
    with pytest.raises(ValueError, match="执行范围"):
        app.start(StartDailyBatch(day=DAY, request_id=request.request_id))
    executor.finish()
    assert seen == [identity] and app.snapshot().job.status == "partial"
    assert app.retry_selection(request).id == job.id and not executor.calls
    with pytest.raises(ValidationError):
        RetryPaperSelection(request_id=uuid4(), selection_run_id="../../private")


def test_composition_retry_uses_frozen_run_and_never_builds_collection(tmp_path, monkeypatch):
    import agent4pharma.composition as composition
    from agent4pharma.infrastructure.persistence.paper_selection_repository import PaperSelectionRepository
    from agent4pharma.modules.discovery.paper_selection import PaperSelectionRun

    run = PaperSelectionRun(
        id="selection-" + "c" * 24,
        batch_id="existing-batch",
        created_at="2026-11-05T00:00:00+00:00",
        items=[],
    )
    PaperSelectionRepository(tmp_path).save(run)
    calls = []

    def screen(config, batch_id, **options):
        calls.append((batch_id, options))
        return run.model_copy(update={"phase": "fulltexts"})

    class ReportReader:
        def report(self, batch_id):
            assert batch_id == run.batch_id
            return {"unique_materials": 12, "coverage_status": "partial"}

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(composition, "screen_batch", screen)
    monkeypatch.setattr(composition, "open_application_storage", lambda _: ReportReader())
    monkeypatch.setattr(composition, "build_daily_literature_discovery", lambda *_: pytest.fail("no collect"))
    app = composition.build_daily_batch_execution(ApplicationSettings(data_dir=tmp_path))
    try:
        outcome = app.retry_operation(run.id, lambda status, batch: calls.append((status, batch)))
        assert outcome.partial and outcome.materials == 12
        assert calls == [
            ("selecting", run.batch_id),
            (run.batch_id, {"run_id": run.id, "retry_models": True}),
            "closed",
        ]
    finally:
        app.close()
