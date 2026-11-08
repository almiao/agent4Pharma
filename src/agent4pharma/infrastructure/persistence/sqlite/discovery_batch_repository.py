"""SQLite 发现批次存取：维护入选状态、批次投影与报告状态。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.coarse_screening import assess_coarse, policy_hash
from agent4pharma.infrastructure.persistence.sqlite.job_repository import JobRepository
from agent4pharma.modules.materials.collection_records import PersistedRecord
from agent4pharma.runtime.settings import ApplicationSettings
from agent4pharma.shared.clock import utcnow
from agent4pharma.shared.serialization import dump


@dataclass
class DiscoveryRepository:
    """批次状态仓储，负责候选和报告投影；不调用外部来源或模型。"""

    root: Path
    db: sqlite3.Connection

    def has_material(self, batch_id: str, material_id: str) -> bool:
        """判断资料是否属于指定发现批次。"""
        return (
            self.db.execute(
                "SELECT 1 FROM discovery_batch_items WHERE batch_id=? AND material_id=?",
                (batch_id, material_id),
            ).fetchone()
            is not None
        )

    def record_report(self, batch_id: str, status: str, report_ref: str) -> None:
        """在同一事务中增加报告版本并更新报告引用与运行覆盖状态。"""
        with self.db:
            self.db.execute(
                "UPDATE discovery_batches SET revision=revision+1,report_ref=? WHERE id=?",
                (report_ref, batch_id),
            )
            self.db.execute("UPDATE jobs SET status=?,updated_at=? WHERE id=?", (status, utcnow(), batch_id))

    def create_run(self, day: str, config: ApplicationSettings) -> tuple[str, str]:
        """按业务日和策略指纹幂等创建运行及发现批次，返回二者 ID。"""
        run_id = f"daily-{day}-{config.fingerprint()}"
        batch_id = run_id
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO jobs VALUES (?,?,?,NULL,0,?,?,NULL,NULL,?)",
                (
                    run_id,
                    "daily",
                    run_id,
                    "pending",
                    dump({"day": day, "policy": config.snapshot()}),
                    utcnow(),
                ),
            )
            self.db.execute(
                "INSERT OR IGNORE INTO discovery_batches VALUES (?,?,?,?,0,NULL)",
                (
                    batch_id,
                    run_id,
                    config.policy_version,
                    dump({"day": day, "timezone": config.timezone}),
                ),
            )
        return run_id, batch_id

    def assess(self, batch_id: str, config: ApplicationSettings) -> None:
        """执行确定性粗筛，留下待语义判断条目，不随机抽样。"""
        assess_coarse(self.db, batch_id, config)

    def items(self, batch_id: str, after: int = 0, limit: int = 100, selected_only: bool = False) -> dict:
        """按条目 ID 分页返回批次记录，limit 为 1..100，可限定已入选资料。"""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be 1..100")
        rows = self.db.execute(
            "SELECT i.*,r.metadata FROM discovery_batch_items i JOIN source_records r "
            "ON r.id=i.source_record_id WHERE i.batch_id=? AND i.id>? "
            + ("AND i.state='selected' " if selected_only else "")
            + "ORDER BY i.id LIMIT ?",
            (batch_id, after, limit + 1),
        ).fetchall()
        result = [dict(row) for row in rows[:limit]]
        for row in result:
            row["metadata"] = json.loads(row["metadata"])
            row["assessment"] = json.loads(row["assessment"]) if row["assessment"] else None
        return {"items": result, "next_cursor": result[-1]["id"] if len(rows) > limit else None}

    def report(self, batch_id: str) -> dict:
        """构造真实覆盖与候选投影；部分来源失败或判断未完成时不得报告 complete。"""
        batch = self.db.execute("SELECT * FROM discovery_batches WHERE id=?", (batch_id,)).fetchone()
        if batch is None:
            raise KeyError(batch_id)
        jobs = [
            JobRepository(self.root, self.db).job(r[0])
            for r in self.db.execute(
                "SELECT id FROM jobs WHERE parent_id=? AND kind='collect' ORDER BY id", (batch["run_id"],)
            )
        ]
        pending = self.db.execute(
            "SELECT count(*) FROM discovery_batch_items WHERE batch_id=? AND state='pending'", (batch_id,)
        ).fetchone()[0]
        states = [j["status"] for j in jobs]
        status = "complete" if states and all(s == "complete" for s in states) and not pending else "partial"
        if states and all(s == "failed" for s in states):
            status = "failed"
        coverage_status = status
        awaiting = self.db.execute(
            "SELECT count(*) FROM discovery_batch_items WHERE batch_id=? AND state='awaiting_intelligence'",
            (batch_id,),
        ).fetchone()[0]
        triage_pending = self.db.execute(
            "SELECT count(*) FROM discovery_batch_items WHERE batch_id=? AND state='awaiting_triage'",
            (batch_id,),
        ).fetchone()[0]
        triage_evidence = self.db.execute(
            "SELECT count(*) FROM discovery_batch_items WHERE batch_id=? AND state='needs_triage_evidence'",
            (batch_id,),
        ).fetchone()[0]
        if (awaiting or triage_pending or triage_evidence) and status == "complete":
            status = "partial"
        for job in jobs:
            cursor = (job["result"] or {}).get("cursor")
            if cursor and "ids" in cursor:
                # ID snapshots are collector state, not model context.
                job["result"]["cursor"] = {k: v for k, v in cursor.items() if k != "ids"}
        rows = self.db.execute(
            "SELECT DISTINCT i.material_id,m.title,m.identity,i.assessment FROM discovery_batch_items i "
            "JOIN materials m ON m.id=i.material_id WHERE batch_id=? AND state='selected'",
            (batch_id,),
        ).fetchall()
        count = self.db.execute(
            "SELECT count(DISTINCT material_id) FROM discovery_batch_items WHERE batch_id=?", (batch_id,)
        ).fetchone()[0]
        candidates = {}
        for row in rows:
            item = dict(row)
            item["assessment"] = json.loads(item["assessment"])
            candidates.setdefault(item["material_id"], item)
        policy = JobRepository(self.root, self.db).job(batch["run_id"])["request"].get("policy", {})
        screening = (
            "historical random pilot"
            if batch["policy_ref"].startswith("random-")
            else "local decider → intelligence semantic assessment"
            if policy.get("triage", {}).get("enabled")
            else "direct intelligence screening (local decider disabled)"
        )
        return {
            "batch_id": batch_id,
            "status": status,
            "coverage_status": coverage_status,
            "intelligence_status": "pending" if awaiting else "complete",
            "window": json.loads(batch["requested_window"]),
            "policy_ref": batch["policy_ref"],
            "unique_materials": count,
            "pending": pending,
            "candidates": list(candidates.values()),
            "sources": jobs,
            "screening": screening,
            "awaiting_intelligence": self.db.execute(
                "SELECT count(*) FROM discovery_batch_items WHERE batch_id=? AND "
                "state='awaiting_intelligence'",
                (batch_id,),
            ).fetchone()[0],
            "awaiting_triage": triage_pending,
            "triage_status": "pending" if triage_pending else "complete",
            "needs_triage_evidence": triage_evidence,
            "generated_at": utcnow(),
        }

    def register_record(self, batch_id: str, stored: PersistedRecord, policy: dict) -> None:
        """按资料既有版本与策略判断批次成员类型；参与页事务，不自行提交。"""
        assessed = self.db.execute(
            "SELECT 1 FROM discovery_batch_items i JOIN discovery_batches b ON b.id=i.batch_id "
            "WHERE i.source_record_id=? AND b.policy_ref=? "
            "AND i.state IN ('selected','not_selected','watch','needs_evidence') "
            "AND json_extract(i.assessment,'$.policy_hash')=? LIMIT 1",
            (stored.record_id, policy["policy_version"], policy_hash(policy)),
        ).fetchone()
        kind = (
            "seen_before"
            if assessed
            else "policy_reevaluation"
            if stored.known
            else "version_update"
            if stored.existed
            else "first_discovered"
        )
        self.db.execute(
            "INSERT OR IGNORE INTO discovery_batch_items"
            "(batch_id,source_record_id,material_id,kind,state) VALUES (?,?,?,?,?)",
            (batch_id, stored.record_id, stored.material_id, kind, "already_seen" if assessed else "pending"),
        )

    def batch_policy(self, batch_id: str) -> dict:
        """读取批次所属运行的不可变策略；批次不存在时抛出 KeyError。"""
        batch = self.db.execute("SELECT run_id FROM discovery_batches WHERE id=?", (batch_id,)).fetchone()
        if batch is None:
            raise KeyError(batch_id)
        return JobRepository(self.root, self.db).job(batch["run_id"])["request"]["policy"]
