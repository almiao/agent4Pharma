"""人工单篇 PDF 的异步 HTTP 适配；请求立即返回，浏览器等待不占 HTTP 请求。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from urllib.parse import parse_qs, urlsplit

from pydantic import JsonValue

from agent4pharma.modules.materials.paper_download_jobs import StartPaperDownload
from agent4pharma.workflows.paper_download_workbench import PaperDownloadWorkbench


class PaperDownloadAPI:
    """单工人执行与重复提交合并，资源由工作台服务显式关闭。"""

    def __init__(self, app: PaperDownloadWorkbench, *, owns_browser: bool = True) -> None:
        self.app = app
        self.owns_browser = owns_browser
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="paper-download")
        self.pending: set[str] = set()
        self.lock = Lock()
        self.connecting = False

    def connection_status(self) -> JsonValue:
        """查询常驻连接，不创建浏览器、不消耗论文预算。"""
        if self.app.browser is None:
            raise ValueError("浏览器连接服务未配置")
        status = self.app.browser.status()
        with self.lock:
            if self.connecting:
                status = status.model_copy(update={"phase": "connecting"})
        return status.model_dump(mode="json")

    def connect(self, payload: JsonValue = None) -> JsonValue:
        """人工触发连接或检查；串行复用运行台所有者，不排队多次授权请求。"""
        if payload not in (None, {}):
            raise ValueError("连接操作不接受网址、脚本或其他参数")
        if self.app.browser is None:
            raise ValueError("浏览器连接服务未配置")
        with self.lock:
            if not self.connecting and not self.pending:
                self.connecting = True
                self.executor.submit(self._connect)
        return {"accepted": True}

    def _connect(self) -> None:
        try:
            self.app.browser.connect()
        finally:
            with self.lock:
                self.connecting = False

    def get(self, path: str) -> JsonValue:
        """读取指定论文状态，不在 GET 中访问外部网站。"""
        query = parse_qs(urlsplit(path).query)
        return self.app.status(query.get("run", [""])[0], query.get("record", [""])[0]).model_dump(
            mode="json"
        )

    def file(self, path: str) -> bytes:
        """返回经绑定、目录和哈希检查的主文，不接受任意路径。"""
        query = parse_qs(urlsplit(path).query)
        return self.app.ready_pdf(query.get("run", [""])[0], query.get("record", [""])[0])

    def post(self, payload: JsonValue) -> JsonValue:
        """显式人工点击才启动；已有任务与已下载文件不会重复消耗额度。"""
        request = StartPaperDownload.model_validate(payload)
        with self.lock:
            if self.connecting:
                raise ValueError("浏览器正在连接，请确认连接完成后再开始下载")
            if self.app.browser is None or self.app.browser.status().phase != "ready":
                raise ValueError("请先连接日常 Chrome，确认浏览器已就绪后再下载")
            job = self.app.start(request)
            if job.phase == "queued" and job.id not in self.pending:
                self.pending.add(job.id)
                self.executor.submit(self._run, job.id)
        return {"accepted": True, "job_id": job.id, "phase": job.phase}

    def _run(self, job_id: str) -> None:
        try:
            self.app.execute(job_id)
        finally:
            with self.lock:
                self.pending.discard(job_id)

    def close(self) -> None:
        """停止接受新任务；正在运行的流程有硬超时，状态不自动重试。"""
        self.executor.shutdown(wait=False, cancel_futures=True)
        if self.app.browser and self.owns_browser:
            self.app.browser.close()
