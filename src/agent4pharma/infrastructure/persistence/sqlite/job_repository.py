"""SQLite 任务存取：持久化逻辑任务、尝试次数、状态和可恢复结果。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from agent4pharma.shared.clock import utcnow
from agent4pharma.shared.serialization import dump


@dataclass
class JobRepository:
    """任务记录仓储；写入尝试与结果，成功条件由调用用例决定。"""

    root: Path
    db: sqlite3.Connection

    def daily_jobs(self) -> list[dict]:
        """返回每日发现任务及其状态，供调度计算补跑日期。"""
        return [self.job(row[0]) for row in self.db.execute("SELECT id FROM jobs WHERE kind='daily'")]

    def collection_jobs(self, run_id: str) -> list[dict]:
        """读取一个运行的实际来源任务，包含失败、未完成和成功空结果。"""
        return [
            self.job(row[0])
            for row in self.db.execute(
                "SELECT id FROM jobs WHERE parent_id=? AND kind='collect' ORDER BY id", (run_id,)
            )
        ]

    def source_job(self, run_id: str, source: str, request: dict) -> dict:
        """为一个运行和来源幂等创建采集任务；重试保留原请求窗口与已保存游标。"""
        job_id = f"{run_id}:{source}"
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,0,?,?,NULL,NULL,?)",
                (
                    job_id,
                    "collect",
                    job_id,
                    run_id,
                    "pending",
                    dump(request),
                    utcnow(),
                ),
            )
        return self.job(job_id)

    def job(self, job_id: str) -> dict:
        """读取逻辑任务及反序列化的请求/结果；不存在时抛出 KeyError。"""
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        result = dict(row)
        for key in ("request", "result"):
            if result[key] is not None:
                result[key] = json.loads(result[key])
        return result

    def set_job(self, job_id: str, status: str, *, result=None, error=None, attempt=False):
        """保存任务状态、结果和脱敏错误；attempt=True 才增加尝试计数。"""
        with self.db:
            self.db.execute(
                "UPDATE jobs SET status=?,result=?,error=?,attempt=attempt+?,updated_at=? WHERE id=?",
                (status, dump(result) if result is not None else None, error, int(attempt), utcnow(), job_id),
            )
