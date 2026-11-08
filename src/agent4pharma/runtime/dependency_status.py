"""依赖健康状态的公开投影；可用、配置存在和未探测分别表达。"""

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, model_validator


class DependencyControlRequest(BaseModel):
    """只控制预登记的本机依赖；不接受命令、端口或路径，重启须确认中断。"""

    model_config = ConfigDict(extra="forbid")
    service: Literal["mineru", "decider"]
    action: Literal["start", "restart"]
    confirm_interrupt: bool = False

    @model_validator(mode="after")
    def require_restart_confirmation(self) -> "DependencyControlRequest":
        """重启不隐含恢复任何被中断的业务任务。"""
        if self.action == "restart" and not self.confirm_interrupt:
            raise ValueError("重启须明确确认中断影响")
        return self


class DependencyOperation(BaseModel):
    """进程内一次有界服务操作，不把命令退出或进程存在当作健康成功。"""

    id: str
    service: Literal["mineru", "decider"]
    action: Literal["start", "restart"]
    status: Literal["running", "succeeded", "failed"]
    detail: str
    started_at: str


class DependencyStatus(BaseModel):
    """脱敏后的依赖检查，仅公开应用工作目录，不携带凭证、连接地址或原始日志。"""

    id: str
    title: str
    status: Literal[
        "healthy",
        "unavailable",
        "unknown",
        "configured",
        "disabled",
        "not_installed",
        "stopped",
        "starting",
        "permission_error",
        "error",
    ]
    detail: str
    queued: int | None = None
    actions: list[Literal["start", "restart"]] = []
    control_note: str = ""
    work_directory: str = ""
    operation: DependencyOperation | None = None


class DependencySnapshot(BaseModel):
    """带检查时间的依赖集合，未知状态不推断为停止。"""

    checked_at: str
    items: list[DependencyStatus]


class DependencyControl(Protocol):
    """运行台持有的依赖控制资源；读取和显式操作分别调用。"""

    def snapshot(self, configured: bool) -> DependencySnapshot:
        """读取健康与可控能力，不启动服务。"""
        ...

    def control(self, request: DependencyControlRequest) -> DependencyOperation:
        """受理固定服务操作，返回异步状态。"""
        ...

    def close(self) -> None:
        """释放本入口持有的调度资源。"""
        ...


class ParserTask(BaseModel):
    """解析任务公开身份与页码；不回传文件路径或原始异常。"""

    id: int
    short_id: str
    tier: str
    page_range: str
    status: Literal["pending", "done", "parsing", "failed", "superseded"]


class ParserTasks(BaseModel):
    """有界的近期解析任务，保留总数提示截断。"""

    parses: list[ParserTask]
    total: int
    checked_at: str = ""
