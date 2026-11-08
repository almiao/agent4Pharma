"""来源访问额度存取：初始化采集来源，跨进程预约请求并保存上游延期。"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from agent4pharma.runtime.settings import ApplicationSettings
from agent4pharma.shared.serialization import dump


@dataclass
class AccessRepository:
    """来源与访问额度仓储；请求预约使用短事务，等待发生在事务外。"""

    root: Path
    db: sqlite3.Connection

    def defer_access(self, group: str, delay: float) -> None:
        """把共享额度的下一可访问时间至少推迟 delay 秒，供后续进程重试遵守。"""
        with self.db:
            self.db.execute(
                "UPDATE source_access_configs SET next_request_at=max(next_request_at,?) WHERE id=?",
                (time.time() + delay, group),
            )

    def seed_sources(self, config: ApplicationSettings):
        """幂等更新采集来源及共享访问额度配置，保留已有关联元数据。"""
        for source, options in config.sources.items():
            old = self.db.execute("SELECT descriptor FROM sources WHERE id=?", (source,)).fetchone()
            descriptor = {
                **(json.loads(old[0]) if old else {}),
                "id": source,
                "coverage": "Configured bounded current window; no historical coverage claim"
                if options.get("collection_mode") == "configured"
                else "Pilot acquisition scope; see config query and coverage report",
                "capabilities": ["discover", "metadata", "abstract", "fulltext_or_preview"]
                if options.get("collection_mode") == "configured"
                else ["discover", "metadata", "abstract"],
                "query": options.get("query", ""),
            }
            self.db.execute(
                "INSERT INTO sources(id,descriptor) VALUES (?,?) "
                "ON CONFLICT(id) DO UPDATE SET descriptor=excluded.descriptor",
                (source, dump(descriptor)),
            )
            # bioRxiv and medRxiv share the same host and rate budget.
            access_id = "biorxiv-api" if source in {"biorxiv", "medrxiv"} else source
            self.db.execute(
                "INSERT INTO source_access_configs(id,source_id,config) VALUES (?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET config=excluded.config",
                (access_id, source, dump({"connector": source})),
            )
        self.db.commit()

    def reserve_request(self, group: str, interval: float) -> float:
        """Cross-process reservation; the caller sleeps outside the transaction."""
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT next_request_at FROM source_access_configs WHERE id=?", (group,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown access group: {group}")
            now = time.time()
            slot = max(now, row[0])
            self.db.execute(
                "UPDATE source_access_configs SET next_request_at=? WHERE id=?", (slot + interval, group)
            )
            self.db.commit()
            return max(0, slot - now)
        except BaseException:
            self.db.rollback()
            raise
