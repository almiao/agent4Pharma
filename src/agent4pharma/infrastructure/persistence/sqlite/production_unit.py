"""在一个短事务中装配制作业务仓储，不扩大既有集中存储门面。"""

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from agent4pharma.infrastructure.persistence.sqlite.production_repository import (
    CommissionRepository,
    DraftRepository,
    ProductionTaskRepository,
    ReleaseRepository,
    ReviewRepository,
)
from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction


@dataclass
class SqliteProductionUnit:
    """同一连接上的窄仓储集合，连接不向应用层暴露。"""

    stories: CommissionRepository
    drafts: DraftRepository
    reviews: ReviewRepository
    releases: ReleaseRepository
    tasks: ProductionTaskRepository


@contextmanager
def production_unit(root: Path, *, read_only: bool = False) -> Iterator[SqliteProductionUnit]:
    """原子提交或回滚；只读快照禁止写入、不抢占写租约，事务内不做网络或模型调用。"""
    with transaction(root) as connection:
        if read_only:
            connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN" if read_only else "BEGIN IMMEDIATE")
        yield SqliteProductionUnit(
            CommissionRepository(connection),
            DraftRepository(connection),
            ReviewRepository(connection),
            ReleaseRepository(connection),
            ProductionTaskRepository(connection),
        )
