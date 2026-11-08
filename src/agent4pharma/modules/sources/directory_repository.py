"""Public source-directory operations; legacy JSON projections stay compatible."""

from pathlib import Path
from typing import Protocol


class SourceDirectoryRepository(Protocol):
    """共享来源目录的导入和查询契约；demo 导入必须整体提交或回滚。"""

    root: Path

    def import_catalog(self, seed_path: Path, inventory_path: Path) -> dict:
        """在同一事务中导入整理期刊、外部观察标签和出处，重复导入不新增重复记录。"""
        ...

    def list_catalog(self, status: str | None = None, limit: int = 200) -> list[dict]:
        """按关注状态查询载体与最近指标；缺失指标保持 None，不转换为零。"""
        ...

    def import_demo(self, seed_path: Path) -> dict:
        """原子导入 demo 命名空间的目录和资料；所有新入口关闭，不进入生产发现批次。"""
        ...

    def inspect_demo(self) -> dict:
        """返回 demo 数据集的目录、资料和来源关系投影，不执行网络请求。"""
        ...

    def demo_endpoints(self) -> list[dict]:
        """查询 demo 来源入口及运营机构关系，供报告展示真实数据库状态。"""
        ...
