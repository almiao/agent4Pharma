"""采集页工作单元：资料、批次成员和检查点共用一个连接及事务，失败整页回滚。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.discovery_batch_repository import DiscoveryRepository
from agent4pharma.infrastructure.persistence.sqlite.material_repository import MaterialRepository
from agent4pharma.modules.materials.collection_records import CollectionPage
from agent4pharma.shared.clock import utcnow
from agent4pharma.shared.serialization import dump


@dataclass
class CollectionRepository:
    """单页采集事务的所有者，协调资料仓储与发现仓储，共享同一连接。"""

    root: Path
    db: sqlite3.Connection

    def ensure_task(self, scope_id: str, source: str, settings: dict) -> dict:
        """幂等创建来源采集范围并返回状态，供初始/重叠窗口计算使用。"""
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO collection_tasks"
                "(id,source_id,scope,checkpoint,last_success_at) VALUES (?,?,?,NULL,NULL)",
                (scope_id, source, dump(settings)),
            )
        return dict(self.db.execute("SELECT * FROM collection_tasks WHERE id=?", (scope_id,)).fetchone())

    def commit_page(self, batch_id: str, job_id: str, page: CollectionPage, scope_id: str):
        """在一个事务内保存全部资料、批次成员和游标；任何一步失败都回滚数据库。"""
        materials = MaterialRepository(self.root, self.db)
        discovery = DiscoveryRepository(self.root, self.db)
        policy = discovery.batch_policy(batch_id)
        with self.db:
            raw_refs = [
                materials.put_artifact(
                    raw.body,
                    "raw_response",
                    {
                        "url": raw.url,
                        "content_type": raw.content_type,
                        "retrieved_at": utcnow(),
                        "job_id": job_id,
                    },
                )
                for raw in page.raw
            ]
            for record in page.records:
                stored = materials.ingest_record(record, raw_refs)
                discovery.register_record(batch_id, stored, policy)
            progress = {"cursor": page.next_cursor, "total": page.total, "date_basis": page.date_basis}
            self.db.execute(
                "UPDATE jobs SET status=?,result=?,error=NULL,updated_at=? WHERE id=?",
                (
                    "complete" if page.next_cursor is None else "running",
                    dump(progress),
                    utcnow(),
                    job_id,
                ),
            )
            self.db.execute(
                "UPDATE collection_tasks SET checkpoint=?,last_success_at=? WHERE id=?",
                (dump(progress), utcnow() if page.next_cursor is None else None, scope_id),
            )
