"""DSH 的绑定论文获取用例；模型不能提供浏览器脚本、网址、文件路径或重试开关。"""

from pydantic import BaseModel

from agent4pharma.modules.materials.paper_downloads import (
    PaperDownloader,
    PaperDownloadLedger,
    PaperDownloadRequest,
    PaperDownloadResult,
)


class BrowserPaperStatus(BaseModel):
    """只展示当前选中论文的元数据和获取状态，不包含浏览器会话或其他标签信息。"""

    title: str
    doi: str
    status: str
    reason: str = ""


class BrowserPaperTools:
    """一个实例只持有一份经宿主校验的全文清单请求，重复调用复用账本。"""

    def __init__(
        self, request: PaperDownloadRequest, downloader: PaperDownloader, ledger: PaperDownloadLedger
    ) -> None:
        self.request, self.downloader, self.ledger = request, downloader, ledger

    def _latest(self) -> PaperDownloadResult | None:
        return next(
            (
                r
                for r in self.ledger.results(self.request.batch_id)
                if r.request.decision_id == self.request.decision_id
            ),
            None,
        )

    def get_browser_paper_status(self) -> BrowserPaperStatus:
        """读取本论文状态，不连接浏览器或启动下载。"""
        result = self._latest()
        return BrowserPaperStatus(
            title=self.request.title,
            doi=self.request.identifier,
            status=result.status if result else "not_started",
            reason=result.reason if result else "",
        )

    def acquire_browser_paper(self) -> BrowserPaperStatus:
        """首次调用才尝试获取；失败也缓存，模型不能自行反复尝试或解除暂停。"""
        if self._latest() is None:
            self.ledger.save(self.downloader.download(self.request))
        return self.get_browser_paper_status()
