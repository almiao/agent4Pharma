"""日常采集至论文精选的执行状态；不授权下载、制作或发布。"""

from datetime import date
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StartDailyBatch(BaseModel):
    """点击时展示的业务日与幂等身份；拒绝跨日误操作和额外执行参数。"""

    model_config = ConfigDict(extra="forbid")
    day: date
    request_id: UUID


class DailyBatchResult(BaseModel):
    """已有流程实际产出的覆盖、数量及精选运行身份。"""

    batch_id: str
    selection_run_id: str
    materials: int
    suggested_downloads: int
    coverage: str
    partial: bool


class RetryPaperSelection(BaseModel):
    """显式重试准确精选运行；不接收采集窗口、模型或池外论文参数。"""

    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    selection_run_id: str = Field(pattern=r"^selection-[a-f0-9]{24}$")


class DailyBatchRun(BaseModel):
    """追加的跑批尝试；失败与中断保留，不在启动或查询时自动补跑。"""

    id: str
    request_ids: tuple[UUID, ...]
    day: date
    status: Literal["queued", "collecting", "selecting", "succeeded", "partial", "failed"]
    created_at: str
    updated_at: str
    batch_id: str = ""
    selection_run_id: str = ""
    result: DailyBatchResult | None = None
    error: str = ""


class DailyBatchRuns(Protocol):
    """跑批元数据端口；网络和模型不在状态写入事务内。"""

    def all(self) -> list[DailyBatchRun]:
        """按创建时间倒序读取执行记录。"""
        ...

    def save(self, run: DailyBatchRun) -> None:
        """原子写入一份执行状态。"""
        ...


class DailyBatchSnapshot(BaseModel):
    """业务日及其最新尝试；空任务与查询失败分别呈现。"""

    day: date
    job: DailyBatchRun | None = None
