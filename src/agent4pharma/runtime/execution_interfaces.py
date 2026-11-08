"""Runtime contracts independent of business modules and storage drivers."""

from typing import Protocol


class Closeable(Protocol):
    """可显式释放的资源；调用方负责在成功和失败路径关闭。"""

    def close(self) -> None:
        """释放本对象拥有的资源；业务状态和已提交数据不随关闭改变。"""
        ...


class JobStore(Protocol):
    """任务状态持久化契约，不承担业务成功条件或重试策略。"""

    def job(self, job_id: str) -> dict:
        """读取逻辑任务及反序列化的请求/结果；不存在时抛出 KeyError。"""
        ...

    def set_job(self, job_id: str, status: str, *, result=None, error=None, attempt=False) -> None:
        """保存任务状态、结果和脱敏错误；attempt=True 才增加尝试计数。"""
        ...


class ScheduleStore(Protocol):
    """调度所需的每日任务与采集覆盖投影，不向调度暴露数据库连接。"""

    def daily_jobs(self) -> list[dict]:
        """返回每日发现任务及其状态，供调度计算补跑日期。"""
        ...

    def report(self, batch_id: str) -> dict:
        """读取覆盖与精筛分别标记的报告，用于只采集任务的完成判断。"""
        ...
