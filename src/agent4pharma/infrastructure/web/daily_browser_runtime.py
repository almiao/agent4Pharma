"""通过官方 Playwright MCP 扩展连接日常浏览器；不启动独立 profile 或导出会话。"""

import asyncio
import json
import os
import re
import sys
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from typing import AsyncIterator, TextIO
from urllib.parse import urlencode, urlsplit

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic import JsonValue

MCP_VERSION = "0.0.84"
EXTENSION_URL = (
    "https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm"
)


class DailyBrowserError(RuntimeError):
    """浏览器连接／工具协议失败；错误只含固定代码，不回显浏览器内容。"""


def browser_failure_reason(error: BaseException) -> str:
    """解开 stdio 退出时的异常组，保留宿主固定原因，不泄露底层浏览器诊断。"""
    if isinstance(error, DailyBrowserError):
        return str(error)
    if isinstance(error, BaseExceptionGroup):
        for nested in error.exceptions:
            reason = browser_failure_reason(nested)
            if reason != "browser_transport_failed":
                return reason
    return "browser_transport_failed"


def _drain_relay_notices(reader: TextIO, publish: Callable[[str], None]) -> None:
    previous = ""
    while line := reader.readline(16384):
        # 仅保留宿主连接地址和固定断线事件，原始浏览器诊断不落盘。
        message = re.sub(r"^\d{4}-\d{2}-\d{2}T[0-9:.]+Z\s+", "", line)
        match = re.match(r"pw:mcp:relay CDP relay server started, extension endpoint: (ws://[^\s]+)", message)
        if match and match.group(1) != previous:
            previous = match.group(1)
            publish(line)
        elif message.startswith("pw:mcp:relay closing extension connection:"):
            publish("browser_disconnected")


@dataclass(frozen=True)
class DailyBrowserRuntime:
    """锁定官方 MCP 版本与输出目录；初始化实例不启动进程或浏览器。"""

    root: Path
    on_connection: Callable[[str], None] | None = None
    request_timeout_seconds: float = 210
    on_disconnect: Callable[[], None] | None = None

    def install(self) -> None:
        """显式安装锁定 npm 依赖，不安装扩展、浏览器或连接用户会话。"""
        import shutil
        import subprocess
        from importlib.resources import files

        import playwright

        npm = shutil.which("npm")
        npm_cli = Path(npm).parent / "node_modules/npm/bin/npm-cli.js" if npm else None
        if npm_cli is None or not npm_cli.is_file():
            raise DailyBrowserError("npm_runtime_missing")
        node = Path(playwright.__file__).parent / "driver" / ("node.exe" if os.name == "nt" else "node")
        target = self.root / "tools/browser-mcp"
        target.mkdir(parents=True, exist_ok=True)
        bundle = files("agent4pharma.infrastructure.web").joinpath("browser_mcp_runtime")
        for filename in ("package.json", "package-lock.json"):
            (target / filename).write_bytes(bundle.joinpath(filename).read_bytes())
        result = subprocess.run(
            [
                str(node),
                str(npm_cli),
                "ci",
                "--prefix",
                str(target.resolve()),
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
            ],
            capture_output=True,
            timeout=180,
            check=False,
        )
        if result.returncode:
            raise DailyBrowserError("browser_runtime_install_failed")
        self.command()

    def command(self) -> tuple[str, list[str]]:
        """只允许 extension 模式，缺安装就失败，不回退到新浏览器或导入 Cookie。"""
        import playwright

        node = Path(playwright.__file__).parent / "driver" / ("node.exe" if os.name == "nt" else "node")
        package = self.root / "tools/browser-mcp/node_modules/@playwright/mcp"
        manifest = package / "package.json"
        if (
            not manifest.is_file()
            or json.loads(manifest.read_text(encoding="utf-8"))["version"] != MCP_VERSION
        ):
            raise DailyBrowserError("browser_runtime_not_installed")
        if not node.is_file():
            raise DailyBrowserError("browser_node_missing")
        profile_args = []
        if os.name == "nt":
            # 子进程缓存改到项目后，仍显式指向原有 Chrome 数据目录，不能新建独立 profile。
            chrome_data = Path(os.environ["LOCALAPPDATA"]) / "Google/Chrome/User Data"
            if not chrome_data.is_dir():
                raise DailyBrowserError("daily_chrome_profile_missing")
            profile_args = ["--user-data-dir", str(chrome_data)]
        return str(node.resolve()), [
            str((package / "cli.js").resolve()),
            "--extension",
            *profile_args,
            "--snapshot-mode",
            "none",
            "--image-responses",
            "omit",
            "--no-webmcp",
            "--console-level",
            "error",
            "--timeout-navigation",
            "30000",
            "--timeout-action",
            "10000",
            "--output-dir",
            str((self.root / "paper-downloads/daily-browser").resolve()),
        ]

    @asynccontextmanager
    async def session(self) -> AsyncIterator[ClientSession]:
        """本地 stdio 资源上下文；诊断经内存管道过滤，不持久化浏览器原文。"""
        command, args = self.command()
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "r", encoding="utf-8", errors="replace")
        diagnostic = os.fdopen(write_fd, "w", encoding="utf-8")
        notices: asyncio.Queue[str] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        drain = Thread(
            target=_drain_relay_notices,
            args=(reader, lambda text: loop.call_soon_threadsafe(notices.put_nowait, text)),
            name="browser-notices",
            daemon=True,
        )
        drain.start()
        try:
            async with stdio_client(
                StdioServerParameters(
                    command=command,
                    args=args,
                    cwd=str(self.root.resolve()),
                    env={**self.environment(), "DEBUG": "pw:mcp:relay"},
                ),
                errlog=diagnostic,
            ) as streams:
                async with ClientSession(
                    *streams, read_timeout_seconds=self.request_timeout_seconds
                ) as client:
                    await client.initialize()
                    available = await client.list_tools()
                    if not any(tool.name == "browser_run_code_unsafe" for tool in available.tools):
                        raise DailyBrowserError("browser_protocol_changed")
                    notice = asyncio.create_task(self._publish_connect_url(notices))
                    try:
                        yield client
                    finally:
                        notice.cancel()
                        await asyncio.gather(notice, return_exceptions=True)
                        if not notice.cancelled() and notice.exception():
                            raise DailyBrowserError("browser_connection_notice_failed") from None
        finally:
            diagnostic.close()
            await asyncio.to_thread(drain.join, 5)
            reader.close()

    async def _publish_connect_url(self, notices: asyncio.Queue[str]) -> None:
        while True:
            raw = await notices.get()
            if raw == "browser_disconnected":
                if self.on_disconnect:
                    self.on_disconnect()
                continue
            match = re.search(r"extension endpoint: (ws://[^\s]+)", raw)
            if match:
                endpoint = match.group(1).rstrip(".")
                parsed = urlsplit(endpoint)
                if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or not re.fullmatch(
                    r"/extension/[a-f0-9-]{36}", parsed.path
                ):
                    continue
                query = urlencode(
                    {
                        "mcpRelayUrl": endpoint,
                        "client": json.dumps({"name": "pharma-browser-paper"}),
                        "protocolVersion": "2",
                    }
                )
                url = "chrome-extension://mmlmfjhmonkocbjadbfplnigmagldckm/connect.html?" + query
                if self.on_connection is not None:
                    self.on_connection(url)
                    continue
                print(
                    "若未弹出连接页，请在日常 Chrome 地址栏打开本次地址并确认：\n" + url,
                    file=sys.stderr,
                    flush=True,
                )

    def environment(self) -> dict[str, str]:
        """将浏览器临时产物限制在项目可写目录，不继承文献口令或修改系统环境。"""
        temporary = self.root.resolve() / "private/chaoslib/browser-temp"
        temporary.mkdir(parents=True, exist_ok=True)
        environment = {name: str(temporary) for name in ("TEMP", "TMP", "TMPDIR")}
        if os.name == "nt":
            cache = self.root.resolve() / "private/chaoslib/browser-cache"
            cache.mkdir(parents=True, exist_ok=True)
            environment["LOCALAPPDATA"] = str(cache)
        return environment

    async def execute(
        self, client: ClientSession, code: str, *, timeout_seconds: float = 180
    ) -> dict[str, JsonValue]:
        """只由宿主静态脚本调用；上层不向模型暴露此方法或原始 MCP 输出。"""
        try:
            result = await asyncio.wait_for(
                client.call_tool("browser_run_code_unsafe", {"code": code}), timeout=timeout_seconds
            )
        except TimeoutError:
            raise DailyBrowserError("browser_connection_or_action_timeout") from None
        if result.is_error:
            raise DailyBrowserError("browser_connection_or_action_failed")
        text = "\n".join(c.text for c in result.content if c.type == "text")
        if "### Result" not in text:
            raise DailyBrowserError("browser_protocol_changed")
        try:
            value, _ = json.JSONDecoder().raw_decode(text.split("### Result", 1)[1].lstrip())
        except (ValueError, TypeError):
            raise DailyBrowserError("browser_protocol_changed") from None
        if not isinstance(value, dict):
            raise DailyBrowserError("browser_protocol_changed")
        return value
