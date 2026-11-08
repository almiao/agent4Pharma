"""人工触发单篇下载的应用流程：固定清单、幂等提交、进度及失败恢复。"""

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from threading import RLock

from agent4pharma.modules.materials.paper_download_jobs import (
    ACTIVE_DOWNLOAD_PHASES,
    PaperBrowserControl,
    PaperDownloadJob,
    PaperDownloadJobStore,
    PaperDownloadObserver,
    PaperDownloadProgress,
    PaperDownloadStatus,
    StartPaperDownload,
)
from agent4pharma.modules.materials.paper_downloads import PaperDownloadRequest, PaperDownloadResult
from agent4pharma.shared.clock import utcnow


class PaperDownloadWorkbench:
    """只由人工 HTTP 操作启动，不拥有选择其他论文或扩大账号预算的能力。"""

    def __init__(
        self,
        store: PaperDownloadJobStore,
        binding: Callable[[str, str], PaperDownloadRequest],
        acquire: Callable[[str, str, bool, PaperDownloadObserver], PaperDownloadResult],
        ready_pdf: Callable[[str, str], bytes],
        lock: Callable[[], AbstractContextManager[None]],
        browser: PaperBrowserControl | None = None,
    ) -> None:
        self.store, self.binding, self.acquire, self.ready_pdf, self.lock = (
            store,
            binding,
            acquire,
            ready_pdf,
            lock,
        )
        self.connections: dict[str, str] = {}
        self.thread_lock = RLock()
        self.browser = browser

    def _jobs(self) -> list[PaperDownloadJob]:
        jobs = self.store.all()
        for job in jobs:
            if job.phase in ACTIVE_DOWNLOAD_PHASES and datetime.fromisoformat(job.expires_at) < datetime.now(
                UTC
            ):
                job.phase, job.reason, job.updated_at = "failed", "download_interrupted", utcnow()
                self.connections.pop(job.id, None)
                self.store.save(job)
        return jobs

    def start(self, request: StartPaperDownload) -> PaperDownloadJob:
        """重复点击复用同任务；同一账号最多一个网页下载流程，失败重试必须显式。"""
        bound = self.binding(request.run_id, request.record_id)
        with self.thread_lock, self.lock():
            jobs = self._jobs()
            previous = next((j for j in jobs if j.request.decision_id == bound.decision_id), None)
            if previous and (previous.phase in ACTIVE_DOWNLOAD_PHASES | {"downloaded"} or not request.retry):
                return previous
            if any(j.phase in ACTIVE_DOWNLOAD_PHASES for j in jobs):
                raise ValueError("已有一篇 PDF 正在获取，请完成或等待该流程结束")
            now = datetime.now(UTC)
            job = PaperDownloadJob(
                id="pdf-" + uuid.uuid4().hex,
                run_id=request.run_id,
                record_id=request.record_id,
                request=bound,
                restore_account=request.restore_account,
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
                expires_at=(now + timedelta(minutes=10)).isoformat(),
            )
            self.store.save(job)
            return job

    def status(self, run_id: str, record_id: str) -> PaperDownloadStatus:
        """刷新持久化状态；读取不连接浏览器、不下载。"""
        bound = self.binding(run_id, record_id)
        with self.thread_lock, self.lock():
            job = next((j for j in self._jobs() if j.request.decision_id == bound.decision_id), None)
            if job is None:
                try:
                    self.ready_pdf(run_id, record_id)
                except (ValueError, OSError):
                    pass
                else:
                    return PaperDownloadStatus(phase="downloaded", run_id=run_id, record_id=record_id)
                return PaperDownloadStatus(phase="not_started", run_id=run_id, record_id=record_id)
            return PaperDownloadStatus(
                **{
                    **job.model_dump(mode="json", exclude={"request", "restore_account"}),
                    "connect_url": self.connections.get(job.id, ""),
                }
            )

    def execute(self, job_id: str) -> None:
        """后台执行一次；异常留固定待办，不回显供应商诊断、不自动重登或重试。"""
        with self.thread_lock, self.lock():
            job = self.store.get(job_id)
            if job.phase != "queued":
                return
            job.phase, job.updated_at = "waiting_connection", utcnow()
            self.store.save(job)
        try:
            result = self.acquire(
                job.run_id,
                job.record_id,
                job.restore_account,
                lambda progress: self._progress(job.id, progress),
            )
            phase = "downloaded" if result.status == "downloaded" else "manual_required"
            reason, result_id = result.reason, result.id
        except Exception:  # 后台任务边界：异常不能冒充已下载，也不能把连接诊断交给页面。
            phase, reason, result_id = "failed", "download_execution_failed", ""
        with self.thread_lock, self.lock():
            latest = self.store.get(job.id)
            if latest.phase in ACTIVE_DOWNLOAD_PHASES:
                latest.phase, latest.reason, latest.result_id, latest.updated_at = (
                    phase,
                    reason,
                    result_id,
                    utcnow(),
                )
                self.store.save(latest)
            self.connections.pop(job.id, None)

    def _progress(self, job_id: str, progress: PaperDownloadProgress) -> None:
        with self.thread_lock, self.lock():
            job = self.store.get(job_id)
            if job.phase not in ACTIVE_DOWNLOAD_PHASES:
                return
            job.phase, job.updated_at = progress.phase, utcnow()
            self.connections[job_id] = progress.connect_url
            self.store.save(job)
