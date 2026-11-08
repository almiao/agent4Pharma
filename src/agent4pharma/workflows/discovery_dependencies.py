"""Composed workflow capabilities; database connections stay in persistence."""

from pathlib import Path
from typing import Protocol

from agent4pharma.modules.collection.acquisition_interfaces import CollectionPersistence
from agent4pharma.runtime.settings import ApplicationSettings


class DailyDiscoveryStorage(CollectionPersistence, Protocol):
    """每日发现需要的持久化能力组合；页面提交必须保证记录与检查点原子性。"""

    root: Path

    def seed_sources(self, config: ApplicationSettings) -> None:
        """幂等更新采集来源及共享访问额度配置，保留已有关联元数据。"""
        ...

    def create_run(self, day: str, config: ApplicationSettings) -> tuple[str, str]:
        """按业务日和策略指纹幂等创建运行及发现批次，返回二者 ID。"""
        ...

    def assess(self, batch_id: str, config: ApplicationSettings) -> None:
        """把新资料交本地预选；关键词不参与已采集资料的过滤。"""
        ...

    def write_report(self, batch_id: str) -> dict:
        """写出批次报告并保存引用和状态，返回实际覆盖投影。"""
        ...
