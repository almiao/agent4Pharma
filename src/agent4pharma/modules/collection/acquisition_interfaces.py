"""采集所需的外部能力契约：来源分页、采集进度存取和请求额度；不绑定 SQLite/HTTP。"""

from datetime import datetime
from typing import Protocol

from pydantic import AwareDatetime, BaseModel, Field

from agent4pharma.modules.materials.collection_records import CollectionPage
from agent4pharma.runtime.execution_interfaces import JobStore


class CollectionDiagnostic(BaseModel):
    """一次实际失败复核；不覆盖原任务错误，也不代表成功采集或已安排重试。"""

    source_id: str
    url: str
    checked_at: AwareDatetime
    http_status: int | None = Field(default=None, ge=100, le=599)
    reason: str = Field(max_length=1000)


class SourceConnector(Protocol):
    """取得一个来源页；游标为不透明状态，失败必须抛错而非返回伪造空页。"""

    def page(self, start: datetime, end: datetime, query: str, cursor: dict | None) -> CollectionPage:
        """获取 [start, end) 对应来源窗口的一页；游标为空开始扫描，来源失败显式抛错。"""
        ...


class CollectionPersistence(JobStore, Protocol):
    """采集进度持久化契约；完成页面时原子保存资料、成员关系与游标。"""

    def ensure_collection_task(self, scope_id: str, source: str, settings: dict) -> dict:
        """幂等创建采集范围记录并返回保存状态，不覆盖已有检查点。"""
        ...

    def source_job(self, run_id: str, source: str, request: dict) -> dict:
        """为一个运行和来源幂等创建采集任务；重试保留原请求窗口与已保存游标。"""
        ...

    def commit_page(self, batch_id: str, job_id: str, page: CollectionPage, scope_id: str) -> None:
        """原子保存单页资料、批次成员与下一游标；失败整页回滚数据库。"""
        ...


class SourceAccessPersistence(Protocol):
    """请求额度与原始响应存取契约；不向 HTTP 层暴露 SQL 或数据库连接。"""

    def reserve_request(self, group: str, interval: float) -> float:
        """原子预约共享访问额度，返回需要等待的秒数；实际等待必须发生在事务之外。"""
        ...

    def defer_access(self, group: str, delay: float) -> None:
        """把共享额度的下一可访问时间至少推迟 delay 秒，供后续进程重试遵守。"""
        ...

    def archive_response(self, body: bytes, provenance: dict) -> str:
        """保存原始成功响应及出处，并提交本次留档，解析失败也可追溯响应。"""
        ...
