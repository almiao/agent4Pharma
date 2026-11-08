"""人工触发单篇 PDF 获取的任务与进度契约；连接地址仅在当前进程内短暂保存。"""

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from agent4pharma.modules.materials.paper_downloads import PaperDownloadRequest

DownloadPhase = Literal[
    "queued", "waiting_connection", "downloading", "validating", "downloaded", "manual_required", "failed"
]
ACTIVE_DOWNLOAD_PHASES = {"queued", "waiting_connection", "downloading", "validating"}


class PaperDownloadProgress(BaseModel):
    """宿主产生的流程进度；connect_url 不进入持久化任务或下载账本。"""

    phase: Literal["waiting_connection", "downloading", "validating"]
    connect_url: str = ""


class PaperDownloadObserver(Protocol):
    """接收不含网页正文、账号或浏览器诊断的进度。"""

    def __call__(self, progress: PaperDownloadProgress) -> None:
        """更新本篇任务的当前阶段。"""
        ...


class StartPaperDownload(BaseModel):
    """显式选择一篇冻结清单论文；恢复暂停和失败重试都必须单独表意。"""

    model_config = ConfigDict(extra="forbid")
    run_id: str
    record_id: str
    retry: bool = False
    restore_account: bool = False


class PaperDownloadJob(BaseModel):
    """可恢复查询的人工任务，超时转待办，不在后台自动重试。"""

    id: str
    run_id: str
    record_id: str
    request: PaperDownloadRequest
    phase: DownloadPhase = "queued"
    reason: str = ""
    result_id: str = ""
    restore_account: bool = False
    created_at: str
    updated_at: str
    expires_at: str


class PaperDownloadStatus(BaseModel):
    """页面的单篇状态快照；不包含冻结输入、账号数据或文件路径。"""

    id: str = ""
    run_id: str
    record_id: str
    phase: DownloadPhase | Literal["not_started"]
    reason: str = ""
    result_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    expires_at: str = ""
    connect_url: str = ""


class PaperBrowserStatus(BaseModel):
    """当前运行台持有的浏览器连接状态，地址只在等待授权时短暂显示。"""

    phase: Literal["disconnected", "connecting", "ready", "error"] = "disconnected"
    reason: str = ""
    connect_url: str = ""


class PaperBrowserControl(Protocol):
    """运行台所有人工论文任务共用的连接生命周期，不开放通用浏览器脚本。"""

    def connect(self) -> PaperBrowserStatus:
        """确认选中 Chaoslib 页，不消耗下载次数。"""
        ...

    def status(self) -> PaperBrowserStatus:
        """只读最近观测，不访问网站。"""
        ...

    def close(self) -> None:
        """结束本进程的连接资源，不退出用户 Chrome。"""
        ...


class PaperDownloadJobStore(Protocol):
    """资料模块拥有的下载任务状态端口。"""

    def save(self, job: PaperDownloadJob) -> None:
        """原子保存任务，不持有网络事务。"""
        ...

    def get(self, job_id: str) -> PaperDownloadJob:
        """读取准确任务；不存在时失败。"""
        ...

    def all(self) -> list[PaperDownloadJob]:
        """按创建时间倒序读取人工下载任务。"""
        ...
