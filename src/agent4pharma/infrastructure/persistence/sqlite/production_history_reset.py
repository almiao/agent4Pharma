"""经人工授权切换采编流程时清理制作历史；不开放网页入口、不删除来源与论文。"""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProductionHistoryReset:
    """备份及准确清理计数，供维护记录使用，不包含稿件正文。"""

    backup: Path
    deleted: dict[str, int]
    task_ids: tuple[str, ...]


def reset_production_history(root: Path, backup: Path) -> ProductionHistoryReset:
    """先完整备份再在一个事务中删除制作专属记录，活动队列存在时拒绝。

    仅用于用户明确允许放弃旧流程数据的维护操作。调用方须停止运行台写入并另行处理
    筛选模块拥有的制作引用；本函数不改其内部存储，不删除文件或模型配置。
    """
    database = root / "discovery.sqlite3"
    if not database.is_file() or backup.exists() or backup.resolve() == database.resolve():
        raise ValueError("需要有效原库及尚不存在的独立备份路径")
    backup.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection, connection:
        if connection.execute(
            "SELECT 1 FROM production_tasks WHERE status IN ('running','queued') LIMIT 1"
        ).fetchone():
            raise ValueError("制作仍有运行或排队任务，不能清理历史")
        with closing(sqlite3.connect(backup)) as target:
            connection.backup(target)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM wechat_deliveries LIMIT 1").fetchone():
            raise ValueError("存在公众号交付记录，不能清除准确包与防重依据；请先专项核对远端草稿")
        if connection.execute(
            "SELECT 1 FROM production_tasks WHERE status IN ('running','queued') LIMIT 1"
        ).fetchone():
            raise ValueError("制作队列已变化，不能清理历史")
        identities = tuple(row[0] for row in connection.execute("SELECT id FROM production_tasks"))
        deleted = {}
        deleted["workflow_observations"] = connection.execute(
            "DELETE FROM workflow_observations WHERE subject_ref IN (SELECT id FROM production_tasks)"
        ).rowcount
        # 固定白名单及外键依赖顺序；不是来自页面或模型的任意表名。
        for table in (
            "human_package_approvals",
            "release_receipts",
            "channel_packages",
            "editorial_reviews",
            "reader_reviews",
            "content_drafts",
            "content_evidence",
            "commission_versions",
            "editorial_decisions",
            "production_operator_events",
            "production_tasks",
            "production_stories",
        ):
            deleted[table] = connection.execute(f"DELETE FROM {table}").rowcount
    return ProductionHistoryReset(backup.resolve(), deleted, identities)
