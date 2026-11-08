"""回环地址上的内部工作台 HTTP 服务，严格同源 JSON 修改，静态页面不接触数据库。"""

import gzip
import json
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError

from agent4pharma.composition import (
    build_daily_batch_execution,
    build_maintenance_workbench,
    build_paper_continuation,
    build_paper_download_workbench,
    build_production_workbench,
    build_regression_repository,
    execute_production_task,
)
from agent4pharma.entrypoints.web.api import WorkbenchAPI
from agent4pharma.entrypoints.web.article_regression_api import ArticleRegressionAPI
from agent4pharma.entrypoints.web.console_assets import ASSETS
from agent4pharma.entrypoints.web.paper_continuation_api import PaperContinuationAPI
from agent4pharma.entrypoints.web.paper_download_api import PaperDownloadAPI
from agent4pharma.entrypoints.web.production_api import ProductionAPI
from agent4pharma.entrypoints.web.rss_triage_api import get_rss_triage
from agent4pharma.modules.discovery.daily_batch_runs import RetryPaperSelection, StartDailyBatch
from agent4pharma.runtime.locking import worker_lock
from agent4pharma.runtime.settings import ApplicationSettings
from agent4pharma.workflows.daily_batch_execution import DailyBatchExecution
from agent4pharma.workflows.maintenance_workbench import MaintenanceWorkbench

CONSOLE_PATHS = {
    "/",
    "/console",
    "/settings",
    "/sources",
    "/intelligence",
    "/editorial",
    "/content",
    "/review",
    "/publishing",
}


class WorkbenchHTTPServer(ThreadingHTTPServer):
    """单用户回环服务，进程内随机令牌防止其他网页伪造修改请求。"""

    daemon_threads = True

    def __init__(
        self,
        app: MaintenanceWorkbench,
        port: int = 8765,
        production: ProductionAPI | None = None,
        regression: ArticleRegressionAPI | None = None,
        paper_downloads: PaperDownloadAPI | None = None,
        debug_downloads: PaperDownloadAPI | None = None,
        continuation: PaperContinuationAPI | None = None,
        decider_endpoint: str = "",
        daily_batch: DailyBatchExecution | None = None,
    ):
        self.app, self.api = (
            app,
            WorkbenchAPI(app, continuation.confirm_import if continuation else None, decider_endpoint),
        )
        self.root = app.cases.root
        self.token = secrets.token_urlsafe(32)
        self.style_nonce = secrets.token_urlsafe(24)
        self.production = production
        self.regression = regression
        self.paper_downloads = paper_downloads
        self.debug_downloads = debug_downloads
        self.daily_batch = daily_batch
        super().__init__(("127.0.0.1", port), WorkbenchHandler)

    def server_close(self) -> None:
        """关闭本服务持有的控制执行器，不终止独立的依赖服务。"""
        self.api.close()
        if self.daily_batch:
            self.daily_batch.close()
        super().server_close()


class WorkbenchHandler(BaseHTTPRequestHandler):
    """限制 Host/Origin、请求大小与内容类型；用户文本只由前端按文本显示。"""

    server: WorkbenchHTTPServer

    def log_message(self, format: str, *args) -> None:
        """不把文章 URL、请求正文或模型诊断写入访问日志。"""

    def _origin_allowed(self) -> bool:
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in allowed:
            return False
        origin = self.headers.get("Origin")
        return not origin or (urlsplit(origin).scheme == "http" and urlsplit(origin).netloc in allowed)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        compress = (
            len(body) >= 1024
            and content_type.startswith(("text/", "application/json"))
            and any(part.strip() == "gzip" for part in self.headers.get("Accept-Encoding", "").split(","))
        )
        if compress:
            body = gzip.compress(body, compresslevel=1, mtime=0)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        immutable = (
            re.fullmatch(
                r"/console-assets/assets/[A-Za-z0-9_-]+-[A-Za-z0-9_-]{8}\.(js|css)",
                urlsplit(self.path).path,
            )
            and status == 200
        )
        self.send_header("Cache-Control", "public, max-age=31536000, immutable" if immutable else "no-store")
        self.send_header("Vary", "Accept-Encoding")
        if compress:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' "
            + (f"'nonce-{self.server.style_nonce}'; " if urlsplit(self.path).path in CONSOLE_PATHS else "; ")
            + "img-src 'self' data:; connect-src 'self'; "
            + "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(
            status,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
            "application/json; charset=utf-8",
        )

    def do_GET(self) -> None:
        """返回固定静态资源或经过令牌校验的只读 API。"""
        if not self._origin_allowed():
            self._json(403, {"error": "仅允许本地同源访问"})
            return
        path = urlsplit(self.path).path
        if path == "/console-assets/favicon.svg":
            resource = files("agent4pharma.entrypoints.web").joinpath("static", "live-console", "favicon.svg")
            if resource.is_file():
                self._send(200, resource.read_bytes(), "image/svg+xml")
                return
        if path in CONSOLE_PATHS:
            resource = files("agent4pharma.entrypoints.web").joinpath("static", "live-console", "index.html")
            if not resource.is_file():
                self._json(503, {"error": "新版运行台尚未构建"})
                return
            content = (
                resource.read_text(encoding="utf-8")
                .replace("__WORKBENCH_TOKEN__", self.server.token)
                .replace("__STYLE_NONCE__", self.server.style_nonce)
            )
            self._send(200, content.encode(), "text/html; charset=utf-8")
            return
        if path.startswith("/console-assets/assets/"):
            name = path.removeprefix("/console-assets/assets/")
            if re.fullmatch(r"[A-Za-z0-9_-]+\.(js|css)", name):
                resource = files("agent4pharma.entrypoints.web").joinpath(
                    "static", "live-console", "assets", name
                )
                if resource.is_file():
                    content_type = "text/css" if name.endswith(".css") else "text/javascript"
                    self._send(200, resource.read_bytes(), content_type + "; charset=utf-8")
                    return
        if path.startswith("/assets/") and path.removeprefix("/assets/") in ASSETS:
            name = path.removeprefix("/assets/")
            content = (
                files("agent4pharma.entrypoints.web").joinpath("static", name).read_text(encoding="utf-8")
            )
            content_type = "text/css" if name.endswith(".css") else "text/javascript"
            self._send(200, content.encode(), content_type + "; charset=utf-8")
            return
        if not path.startswith("/api/"):
            self._json(404, {"error": "页面或资源不存在"})
            return
        if not secrets.compare_digest(self.headers.get("X-Workbench-Token", ""), self.server.token):
            self._json(403, {"error": "工作台令牌无效，请刷新页面"})
            return
        if path == "/api/daily-batch" and self.server.daily_batch:
            self._dispatch(lambda: self.server.daily_batch.snapshot().model_dump(mode="json"))
        elif path == "/api/paper-downloads/connection" and self.server.paper_downloads:
            self._dispatch(self.server.paper_downloads.connection_status)
        elif path == "/api/paper-downloads/debug" and self.server.debug_downloads:
            self._dispatch(lambda: self.server.debug_downloads.get(self.path))
        elif path == "/api/paper-downloads/debug/file" and self.server.debug_downloads:
            try:
                self._send(200, self.server.debug_downloads.file(self.path), "application/pdf")
            except (KeyError, ValueError, OSError):
                self._json(404, {"error": "尚无可保存的调试 PDF"})
        elif path == "/api/paper-downloads/file" and self.server.paper_downloads:
            try:
                self._send(200, self.server.paper_downloads.file(self.path), "application/pdf")
            except (KeyError, ValueError, OSError):
                self._json(404, {"error": "未找到可用的已校验 PDF，请刷新获取状态"})
        elif path == "/api/paper-downloads" and self.server.paper_downloads:
            self._dispatch(lambda: self.server.paper_downloads.get(self.path))
        elif path.startswith("/api/rss-triage"):
            self._dispatch(lambda: get_rss_triage(self.server.root, self.path))
        elif path.startswith("/api/article-regressions") and self.server.regression:
            self._dispatch(lambda: self.server.regression.get(path))
        elif path.startswith("/api/production") and self.server.production:
            self._dispatch(lambda: self.server.production.get(self.path))
        else:
            self._dispatch(lambda: self.server.api.get(self.path))

    def do_POST(self) -> None:
        """只接受同源、有令牌、有限大小的 JSON，不允许表单跨站修改。"""
        if not self._origin_allowed() or not secrets.compare_digest(
            self.headers.get("X-Workbench-Token", ""), self.server.token
        ):
            self._json(403, {"error": "仅允许本地同源操作，请刷新页面"})
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self._json(415, {"error": "请求必须为 application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 300_000:
                self._json(413, {"error": "请求为空或超过大小限制"})
                return
            payload = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError):
            self._json(400, {"error": "JSON 请求无效"})
            return
        route = urlsplit(self.path).path
        if route == "/api/daily-batch/retry-selection" and self.server.daily_batch:
            self._dispatch(
                lambda: self.server.daily_batch.retry_selection(
                    RetryPaperSelection.model_validate(payload)
                ).model_dump(mode="json")
            )
        elif route == "/api/daily-batch" and self.server.daily_batch:
            self._dispatch(
                lambda: self.server.daily_batch.start(StartDailyBatch.model_validate(payload)).model_dump(
                    mode="json"
                )
            )
        elif route == "/api/paper-downloads/connect" and self.server.paper_downloads:
            self._dispatch(lambda: self.server.paper_downloads.connect(payload))
        elif route == "/api/paper-downloads/debug" and self.server.debug_downloads:
            self._dispatch(lambda: self.server.debug_downloads.post(payload))
        elif route == "/api/paper-downloads" and self.server.paper_downloads:
            self._dispatch(lambda: self.server.paper_downloads.post(payload))
        elif route.startswith("/api/production") and self.server.production:
            self._dispatch(lambda: self.server.production.post(route, payload))
        else:
            self._dispatch(lambda: self.server.api.post(route, payload))

    def _dispatch(self, operation) -> None:
        try:
            self._json(200, operation())
        except ValidationError as exc:
            self._json(400, {"error": "；".join(e["msg"] for e in exc.errors(include_input=False))[:1500]})
        except KeyError as exc:
            self._json(404, {"error": str(exc)})
        except ValueError as exc:
            self._json(400, {"error": str(exc)[:1000]})
        except Exception as exc:
            self._json(500, {"error": "操作失败：" + type(exc).__name__})


def run_workbench(config_path: Path, config: ApplicationSettings, port: int = 8765) -> None:
    """持有独立工作台进程锁后启动，退出时释放服务与后台资源。"""
    if not 1 <= port <= 65535:
        raise ValueError("端口必须为 1..65535")
    from agent4pharma.composition import build_observation_repository, build_wechat_delivery

    with worker_lock(config.data_dir / "workbench"):
        app = build_maintenance_workbench(config_path, config)
        production = ProductionAPI(
            build_production_workbench(config.data_dir, config.llm.model, config.llm.base_url),
            lambda task_id: execute_production_task(config.data_dir, task_id),
            app.model_available,
            build_observation_repository(config.data_dir),
        )
        production.wechat = build_wechat_delivery(production.app)
        production.wechat.recover_interrupted()
        regression = ArticleRegressionAPI(
            build_regression_repository(config.data_dir),
            build_observation_repository(config.data_dir),
            production.app,
        )
        paper_downloads = PaperDownloadAPI(build_paper_download_workbench(config.data_dir))
        debug_downloads = PaperDownloadAPI(
            build_paper_download_workbench(
                config.data_dir, debug=True, browser_connection=paper_downloads.app.browser
            ),
            owns_browser=False,
        )
        continuation = build_paper_continuation(config, executor=production.executor)
        daily_batch = build_daily_batch_execution(config)
        server = None
        try:
            server = WorkbenchHTTPServer(
                app,
                port,
                production,
                regression,
                paper_downloads,
                debug_downloads,
                continuation,
                config.triage.endpoint if config.triage.enabled else "",
                daily_batch,
            )
            daily_batch.recover_interrupted()
            production.resume_automatic()
            print(f"工作台：http://127.0.0.1:{server.server_port}", flush=True)
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if server:
                server.server_close()
            else:
                daily_batch.close()
            app.close()
            production.close()
            continuation.close()
            debug_downloads.close()
            paper_downloads.close()
