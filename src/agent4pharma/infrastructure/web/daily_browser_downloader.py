"""复用日常 Chrome 的有界文献获取；绝不调用旧登录器或文献站点私有接口。"""

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue

from agent4pharma.infrastructure.persistence.chaoslib_access import ChaosLibAccess
from agent4pharma.infrastructure.web.daily_browser_connection import DailyBrowserConnection
from agent4pharma.infrastructure.web.daily_browser_runtime import DailyBrowserRuntime, browser_failure_reason
from agent4pharma.infrastructure.web.daily_browser_scripts import CHECK_SESSION, acquisition_script
from agent4pharma.infrastructure.web.paper_pdf_validation import validate_pdf
from agent4pharma.modules.materials.paper_download_jobs import PaperDownloadObserver, PaperDownloadProgress
from agent4pharma.modules.materials.paper_downloads import PaperDownloadRequest, PaperDownloadResult
from agent4pharma.runtime.locking import worker_lock
from agent4pharma.shared.clock import utcnow


@dataclass
class DailyBrowserDownloader:
    """同一账号额度沿用原计数和暂停；用户只授权当前论文，不能控制其他页面。"""

    root: Path
    observer: PaperDownloadObserver | None = None
    daily_attempt_limit: int | None = None
    connection: DailyBrowserConnection | None = None

    def _progress(self, phase: str, connect_url: str = "") -> None:
        if self.observer:
            self.observer(PaperDownloadProgress(phase=phase, connect_url=connect_url))

    async def inspect(self) -> str:
        """扩展授权后只返回当前 Chaoslib 页的状态，不读取其他标签或会话凭证。"""
        runtime = DailyBrowserRuntime(self.root)
        async with runtime.session() as client:
            result = await runtime.execute(client, CHECK_SESSION, timeout_seconds=60)
        status = result.get("status")
        return (
            str(status)
            if status in {"ready", "select_chaoslib_tab", "browser_access_blocked", "human_login_required"}
            else "browser_protocol_changed"
        )

    def download(self, request: PaperDownloadRequest) -> PaperDownloadResult:
        """先校验绑定请求和额度，再进行一次浏览器流程；失败让路，不重登、不自动重试。"""
        result = PaperDownloadResult(
            id=uuid.uuid4().hex,
            request=request,
            status="failed",
            identifier=request.identifier,
            channel="daily_browser",
            plugin_version="playwright-mcp-0.0.84",
            runtime_profile={
                "daily_attempt_limit": self.daily_attempt_limit,
                "flow_version": "daily-browser-v3",
                "connection_mode": "workbench_shared" if self.connection else "single_use",
            },
            created_at=utcnow(),
        )
        output = self.root.resolve() / "paper-downloads/daily-browser" / (result.id + ".pdf")
        try:
            code = acquisition_script(request, output)
        except ValueError:
            return result.model_copy(
                update={"status": "manual_required", "reason": "daily_browser_publisher_not_supported"}
            )
        # 所有下载路径共用账号账本，换传输方式不能清除过去的暂停或当日次数。
        with worker_lock(self.root / "private/chaoslib"):
            access = ChaosLibAccess(self.root / "private/chaoslib", daily_limit=self.daily_attempt_limit)
            blocked = access.available()
            if blocked:
                return result.model_copy(update={"status": "manual_required", "reason": blocked})
            output.parent.mkdir(parents=True, exist_ok=True)
            try:
                observed = asyncio.run(self._acquire(code))
            except Exception as exc:  # 受限任务边界，只记录类型，MCP 原文可能含会话信息。
                reason = browser_failure_reason(exc)
                return result.model_copy(update={"status": "manual_required", "reason": reason})
            status = observed.get("status")
            if status == "budget_unavailable":
                reason = observed.get("reason", "budget_guard_failed")
                return result.model_copy(update={"status": "manual_required", "reason": str(reason)})
            step = observed.get("step")
            if step in {
                "catalog",
                "publisher_entry",
                "publisher_navigation",
                "article_navigation",
                "pdf_link",
                "download_wait",
                "save_file",
            }:
                result.attempts.append(
                    {
                        "step": step,
                        "status": status,
                        "error_kind": observed.get("error_kind", ""),
                        "http_status": observed.get("http_status"),
                    }
                )
            if status == "browser_access_blocked":
                access.block("blocked_automation")
            if status != "file_saved":
                allowed = {
                    "select_chaoslib_tab",
                    "human_login_required",
                    "publisher_entry_unavailable",
                    "publisher_confirmation_required",
                    "publisher_article_not_found",
                    "publisher_tabs_require_human",
                    "publisher_origin_requires_human",
                    "publisher_verification_required",
                    "pdf_link_requires_human",
                    "pdf_save_requires_human",
                    "browser_access_blocked",
                    "browser_action_failed",
                }
                return result.model_copy(
                    update={
                        "status": "manual_required",
                        "reason": str(status) if status in allowed else "browser_protocol_changed",
                    }
                )
            if not output.is_file():
                return result.model_copy(update={"status": "failed", "reason": "browser_file_missing"})
            self._progress("validating")
            return result.model_copy(update=validate_pdf(output, request.identifier, request.title))

    async def _acquire(self, code: str) -> dict[str, JsonValue]:
        self._progress("waiting_connection")
        if self.connection:
            state = await asyncio.to_thread(
                self.connection.check,
                30,
                lambda url: self._progress("waiting_connection", url),
                allow_connect=False,
            )
            if state != "ready":
                return {"status": state}
            blocked = self._reserve_publisher_attempt()
            if blocked:
                return {"status": "budget_unavailable", "reason": blocked}
            self._progress("downloading")
            return await asyncio.to_thread(self.connection.execute, code, timeout=240, allow_connect=False)
        runtime = DailyBrowserRuntime(
            self.root, lambda url: self._progress("waiting_connection", url), request_timeout_seconds=270
        )
        async with runtime.session() as client:
            state = await runtime.execute(client, CHECK_SESSION, timeout_seconds=180 if self.observer else 60)
            if state.get("status") != "ready":
                return state
            blocked = self._reserve_publisher_attempt()
            if blocked:
                return {"status": "budget_unavailable", "reason": blocked}
            self._progress("downloading")
            return await runtime.execute(client, code, timeout_seconds=240)

    def _reserve_publisher_attempt(self) -> str:
        # download 持有账号锁直至文件校验结束，连接等待不改变原账本。
        return ChaosLibAccess(self.root / "private/chaoslib", daily_limit=self.daily_attempt_limit).reserve()
