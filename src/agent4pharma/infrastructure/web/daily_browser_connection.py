"""运行台内常驻的 stdio 浏览器连接；一个所有者协程管理完整 SDK 生命周期。"""

import asyncio
from collections.abc import Callable
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from threading import Event, RLock, Thread

from pydantic import JsonValue

from agent4pharma.infrastructure.web.daily_browser_runtime import (
    DailyBrowserError,
    DailyBrowserRuntime,
    browser_failure_reason,
)
from agent4pharma.infrastructure.web.daily_browser_scripts import CHECK_SESSION
from agent4pharma.modules.materials.paper_download_jobs import PaperBrowserStatus


@dataclass
class _BrowserCommand:
    code: str
    timeout: float
    notice: Callable[[str], None] | None
    result: asyncio.Future[dict[str, JsonValue]]
    allow_connect: bool


class DailyBrowserConnection:
    """所有任务串行复用同一已授权标签；初始化实例不启动线程、浏览器或读取凭证。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock, self._started = RLock(), Event()
        self._thread: Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[_BrowserCommand] | None = None
        self._owner: asyncio.Task | None = None
        self._current: _BrowserCommand | None = None
        self._status = PaperBrowserStatus()
        self._closed = False
        self._disconnect_epoch = 0

    def status(self) -> PaperBrowserStatus:
        """返回进程内状态快照；不轮询网页或自动重连。"""
        with self._lock:
            return self._status.model_copy()

    def _set(self, phase: str, reason: str = "", url: str = "") -> None:
        with self._lock:
            self._status = PaperBrowserStatus(phase=phase, reason=reason, connect_url=url)

    def _notice(self, url: str) -> None:
        with self._lock:
            if self._status.phase == "ready":
                return
            self._status = PaperBrowserStatus(phase="connecting", connect_url=url)
        if self._current and self._current.notice:
            self._current.notice(url)

    def _disconnected(self) -> None:
        with self._lock:
            if not self._closed:
                self._disconnect_epoch += 1
                self._status = PaperBrowserStatus(phase="error", reason="browser_connection_required")

    def _start(self) -> None:
        with self._lock:
            if self._closed:
                raise DailyBrowserError("browser_connection_closed")
            if self._thread is None or not self._thread.is_alive():
                self._started.clear()
                self._thread = Thread(target=self._thread_main, name="paper-browser", daemon=True)
                self._thread.start()
        if not self._started.wait(5):
            raise DailyBrowserError("browser_connection_start_timeout")

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._serve())
        except asyncio.CancelledError:
            pass
        except Exception:
            self._set("error", "browser_connection_worker_failed")

    async def _serve(self) -> None:
        self._loop, self._queue = asyncio.get_running_loop(), asyncio.Queue()
        self._owner = asyncio.current_task()
        self._started.set()
        try:
            while True:
                command = await self._queue.get()
                if command.result.cancelled():
                    continue
                if not command.allow_connect:
                    command.result.set_exception(DailyBrowserError("browser_connection_required"))
                    continue
                runtime = DailyBrowserRuntime(
                    self.root, self._notice, request_timeout_seconds=660, on_disconnect=self._disconnected
                )
                self._current = command
                try:
                    async with runtime.session() as client:
                        while True:
                            self._current = command
                            if not command.result.cancelled():
                                value = await runtime.execute(
                                    client, command.code, timeout_seconds=command.timeout
                                )
                                if not command.result.done():
                                    command.result.set_result(value)
                            command = await self._queue.get()
                except asyncio.CancelledError:
                    if not command.result.done():
                        command.result.set_exception(DailyBrowserError("browser_connection_closed"))
                    raise
                except Exception as error:
                    reason = browser_failure_reason(error)
                    self._set("error", reason)
                    if not command.result.done():
                        command.result.set_exception(DailyBrowserError(reason))
                finally:
                    self._current = None
        finally:
            while not self._queue.empty():
                command = self._queue.get_nowait()
                if not command.result.done():
                    command.result.set_exception(DailyBrowserError("browser_connection_closed"))

    async def _enqueue(
        self, code: str, timeout: float, notice: Callable[[str], None] | None, allow_connect: bool
    ) -> dict[str, JsonValue]:
        future = asyncio.get_running_loop().create_future()
        await self._queue.put(_BrowserCommand(code, timeout, notice, future, allow_connect))
        return await future

    def execute(
        self,
        code: str,
        timeout: float = 180,
        notice: Callable[[str], None] | None = None,
        *,
        allow_connect: bool = True,
    ) -> dict[str, JsonValue]:
        """仅供宿主固定脚本调用；不是 MCP 工具，SDK 始终在所有者线程内访问。"""
        self._start()
        future = asyncio.run_coroutine_threadsafe(
            self._enqueue(code, timeout, notice, allow_connect), self._loop
        )
        try:
            return future.result(timeout + 20)
        except FutureTimeoutError:
            future.cancel()
            raise DailyBrowserError("browser_connection_or_action_timeout") from None

    def check(
        self, timeout: float = 600, notice: Callable[[str], None] | None = None, *, allow_connect: bool = True
    ) -> str:
        """仅检查当前授权标签；未就绪则保留准确原因，不消费论文额度。"""
        if not allow_connect and self.status().phase != "ready":
            raise DailyBrowserError("browser_connection_required")
        if self.status().phase != "ready":
            self._set("connecting")
        with self._lock:
            epoch = self._disconnect_epoch
        result = self.execute(CHECK_SESSION, timeout, notice, allow_connect=allow_connect)
        status = result.get("status")
        if status == "ready":
            with self._lock:
                if epoch != self._disconnect_epoch:
                    return "browser_connection_required"
                self._status = PaperBrowserStatus(phase="ready")
            return "ready"
        allowed = {"select_chaoslib_tab", "browser_access_blocked", "human_login_required"}
        reason = str(status) if status in allowed else "browser_protocol_changed"
        self._set("error", reason)
        return reason

    def connect(self) -> PaperBrowserStatus:
        """首次授权最多等待十分钟，后续检查复用常驻连接，不重新弹授权页。"""
        try:
            self.check()
        except Exception as error:
            self._set("error", browser_failure_reason(error))
        return self.status()

    def close(self) -> None:
        """由运行台关闭资源，取消本进程的 MCP 等待，不关闭日常 Chrome。"""
        with self._lock:
            self._closed = True
            loop, owner, thread = self._loop, self._owner, self._thread
        if loop and owner and not loop.is_closed():
            loop.call_soon_threadsafe(owner.cancel)
        if thread:
            thread.join(10)
        self._set("disconnected")
