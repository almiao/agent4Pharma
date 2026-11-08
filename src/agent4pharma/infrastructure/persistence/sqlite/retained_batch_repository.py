"""活动汇总批次接收补采结果，以及按真实引用清理过期发现历史。"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction
from agent4pharma.shared.clock import utcnow
from agent4pharma.shared.serialization import dump


@dataclass(frozen=True)
class HistoryCleanupResult:
    """本次数据库删除量和已经没有数据库引用的证据文件；文件由入口校验后删除。"""

    batches: int
    records: int
    artifacts: int
    obsolete_files: list[str]
    retained_batches: list[str]


@dataclass
class RetainedBatchRepository:
    """只处理用户指定活动批次和可证明无引用的历史；调用者须持有 worker_lock。"""

    root: Path

    def merge_retry(self, active_batch: str, retry_batch: str) -> int:
        """将补采成员与实际状态追加到活动归档批次，保留原评估身份和原父批次。

        不改原采集/模型请求，不覆盖活动批次已有来源版本；补采失败来源同样展示。
        只允许归档汇总批次接收不同的既有补采批次，原批次和原模型任务保留可追溯。
        """
        with transaction(self.root) as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT kind FROM jobs WHERE id=?", (active_batch,)).fetchone()
            if active is None or active[0] != "archive_import" or active_batch == retry_batch:
                raise ValueError("只能将不同的补采批次并入活动归档批次")
            if db.execute("SELECT 1 FROM discovery_batches WHERE id=?", (retry_batch,)).fetchone() is None:
                raise KeyError("补采批次不存在")
            count = 0
            for row in db.execute(
                "SELECT * FROM discovery_batch_items WHERE batch_id=? ORDER BY id", (retry_batch,)
            ).fetchall():
                assessment = json.loads(row["assessment"] or "{}")
                assessment["origin_batch"] = retry_batch
                if not assessment.get("intelligence_task"):
                    task = db.execute(
                        "SELECT id FROM intelligence_tasks WHERE json_extract(request,'$.parent_ref')=? "
                        "AND json_extract(request,'$.material_ref')=? "
                        "AND json_extract(request,'$.evidence[0].id')=? "
                        "ORDER BY updated_at DESC,id DESC LIMIT 1",
                        (retry_batch, row["material_id"], row["source_record_id"]),
                    ).fetchone()
                    if task:
                        assessment["intelligence_task"] = task[0]
                cursor = db.execute(
                    "INSERT OR IGNORE INTO discovery_batch_items(batch_id,source_record_id,material_id,"
                    "kind,state,assessment,assessment_revision) VALUES (?,?,?,'retry_import',?,?,?)",
                    (
                        active_batch,
                        row["source_record_id"],
                        row["material_id"],
                        row["state"],
                        dump(assessment),
                        row["assessment_revision"],
                    ),
                )
                count += cursor.rowcount
                db.execute(
                    "UPDATE discovery_batch_items SET kind='retry_import',state=?,assessment=?,"
                    "assessment_revision=? WHERE batch_id=? AND source_record_id=? "
                    "AND kind IN ('archive_rescue','retry_import')",
                    (
                        row["state"],
                        dump(assessment),
                        row["assessment_revision"],
                        active_batch,
                        row["source_record_id"],
                    ),
                )
                db.execute(
                    "INSERT OR IGNORE INTO rss_triage_links(batch_id,source_record_id,decision_id) "
                    "SELECT ?,source_record_id,decision_id FROM rss_triage_links "
                    "WHERE batch_id=? AND source_record_id=?",
                    (active_batch, retry_batch, row["source_record_id"]),
                )
            for row in db.execute(
                "SELECT * FROM jobs WHERE parent_id=? AND kind='collect'", (retry_batch,)
            ).fetchall():
                request = json.loads(row["request"])
                job_id = f"{active_batch}:{request['source']}"
                request["origin_batch"] = retry_batch
                db.execute(
                    "INSERT INTO jobs(id,kind,logical_key,parent_id,attempt,status,request,result,error,"
                    "updated_at) VALUES (?,'collect',?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                    "attempt=excluded.attempt,status=excluded.status,request=excluded.request,"
                    "result=excluded.result,error=excluded.error,updated_at=excluded.updated_at",
                    (
                        job_id,
                        job_id,
                        active_batch,
                        row["attempt"],
                        row["status"],
                        dump(request),
                        row["result"],
                        row["error"],
                        row["updated_at"],
                    ),
                )
            return count

    def prune_history(self, keep_batches: list[str]) -> HistoryCleanupResult:
        """原子删除非活动且不被评估、制作或回归引用的旧批次，再清理无引用来源版本。

        策略、来源目录、选题、稿件、审核、候选、评估及其证据不删除；产物的 raw_refs
        递归闭包也保留。返回可删除文件清单，不直接操作文件。调用者须先保存一致备份。
        """
        with transaction(self.root) as db:
            db.execute("BEGIN IMMEDIATE")
            batches = {r[0] for r in db.execute("SELECT id FROM discovery_batches")}
            if not set(keep_batches) <= batches:
                raise ValueError("保留批次不存在，停止清理")
            references = []
            for table in (
                "intelligence_tasks",
                "intelligence_readings",
                "editorial_candidates",
                "production_tasks",
                "production_stories",
                "content_evidence",
                "content_drafts",
                "editorial_reviews",
                "channel_packages",
                "release_receipts",
                "article_regressions",
                "investigation_evidence",
                "workflow_observations",
            ):
                for row in db.execute(f"SELECT * FROM {table}"):
                    references.extend(value for value in row if isinstance(value, str))
            protected = set(keep_batches) | {
                batch for batch in batches if any(batch in value for value in references)
            }
            discarded = batches - protected
            for batch in discarded:
                for table in ("rss_triage_links", "rss_triage_errors", "discovery_batch_items"):
                    db.execute(f"DELETE FROM {table} WHERE batch_id=?", (batch,))
                run = db.execute("SELECT run_id FROM discovery_batches WHERE id=?", (batch,)).fetchone()[0]
                db.execute("DELETE FROM discovery_batches WHERE id=?", (batch,))
                db.execute("DELETE FROM jobs WHERE parent_id=? AND kind='collect'", (run,))
                db.execute("DELETE FROM jobs WHERE id=?", (run,))
            hashes = {match for value in references for match in re.findall(r"\b[a-f0-9]{64}\b", value)}
            live_records = {r[0] for r in db.execute("SELECT source_record_id FROM discovery_batch_items")}
            live_records |= hashes
            obsolete_records = {r[0] for r in db.execute("SELECT id FROM source_records")} - live_records
            artifacts = {r["id"]: r for r in db.execute("SELECT * FROM artifacts")}
            live_artifacts = hashes | {
                aid for aid, row in artifacts.items() if row["source_record_id"] in live_records
            }
            while True:
                dependencies = {
                    match
                    for aid in live_artifacts
                    if aid in artifacts
                    for match in re.findall(r"\b[a-f0-9]{64}\b", artifacts[aid]["provenance"])
                }
                expanded = live_artifacts | dependencies
                if expanded == live_artifacts:
                    break
                live_artifacts = expanded
            obsolete_artifacts = set(artifacts) - live_artifacts
            db.executemany("DELETE FROM artifacts WHERE id=?", [(aid,) for aid in obsolete_artifacts])
            db.executemany("DELETE FROM source_records WHERE id=?", [(rid,) for rid in obsolete_records])
            db.execute(
                "DELETE FROM rss_triage_decisions WHERE id NOT IN (SELECT decision_id FROM rss_triage_links) "
                "AND json_extract(payload,'$.input.source_record_id') NOT IN (SELECT id FROM source_records)"
            )
            used_paths = {r[0] for r in db.execute("SELECT storage_uri FROM artifacts")}
            obsolete_paths = sorted(
                {artifacts[aid]["storage_uri"] for aid in obsolete_artifacts} - used_paths
            )
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("清理造成外键异常，已回滚")
            db.execute(
                "INSERT INTO maintenance_audit(kind,entity_id,action,detail,created_at) VALUES (?,?,?,?,?)",
                (
                    "discovery",
                    ",".join(keep_batches),
                    "prune_history",
                    dump(
                        {
                            "removed_batches": sorted(discarded),
                            "records": len(obsolete_records),
                            "artifacts": len(obsolete_artifacts),
                        }
                    ),
                    utcnow(),
                ),
            )
            return HistoryCleanupResult(
                len(discarded),
                len(obsolete_records),
                len(obsolete_artifacts),
                obsolete_paths,
                sorted(protected),
            )
