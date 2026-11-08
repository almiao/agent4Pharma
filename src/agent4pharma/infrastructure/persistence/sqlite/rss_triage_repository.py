"""发现模块的统一预选历史、错误和原子路由；覆盖 RSS 与 API 等全部来源。"""

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue

from agent4pharma.infrastructure.connectors.abstract_lookup import is_placeholder
from agent4pharma.infrastructure.persistence.sqlite.job_repository import JobRepository
from agent4pharma.infrastructure.persistence.sqlite.material_repository import MaterialRepository
from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction
from agent4pharma.modules.discovery.triage_contracts import (
    SignalField,
    TriageDecision,
    TriageInput,
    TriagePolicy,
)
from agent4pharma.shared.clock import utcnow
from agent4pharma.shared.serialization import dump


@dataclass
class RssTriageRepository:
    """短连接保存成功判别与来源项交接；表名保留历史，语义已扩到所有来源。"""

    root: Path

    def policy_snapshot(self, batch_id: str) -> TriagePolicy | None:
        """读取运行任务公开快照中的准确提示词；早期未冻结内容的历史批次保持未知。"""
        with transaction(self.root) as db:
            request = JobRepository(self.root, db).job(batch_id)["request"]
        definition = request["policy"].get("triage", {}).get("policy_json", "")
        return TriagePolicy.model_validate_json(definition) if definition else None

    def inputs(self, batch_id: str) -> list[TriageInput]:
        """待判别资料逐版本读入；非 RSS 来源把原生摘要作为判别文本。

        所有已采集资料都经过同一判别器，不能用来源类型决定是否进入详情分析。
        同篇多来源仍由内容身份去重，避免同一篇论文重复推理。
        """
        with transaction(self.root) as db:
            rows = db.execute(
                "SELECT material_id,source_record_id FROM discovery_batch_items "
                "WHERE batch_id=? AND state='awaiting_triage' ORDER BY id",
                (batch_id,),
            ).fetchall()
            materials = MaterialRepository(self.root, db)
            results = []
            for row in rows:
                record = materials.source_record_snapshot(row["source_record_id"])
                signals = record.metadata.get("rss_signal") or {}
                if not isinstance(signals, dict):
                    raise ValueError("RSS 语义字段不是结构化对象")
                fields = [
                    SignalField(
                        field=str(field["field"]),
                        text=str(field["text"]),
                        truncated=bool(field.get("truncated", False)),
                    )
                    for field in signals.get("fields", [])
                ]
                information_status = str(signals.get("information_status", "title_only"))
                extraction_version = str(signals.get("profile_version", ""))
                journal = str(signals.get("journal", ""))
                if not fields and record.abstract and not is_placeholder(record.abstract):
                    fields = [
                        SignalField(
                            field="abstract",
                            text=record.abstract[:12000],
                            truncated=len(record.abstract) > 12000,
                        )
                    ]
                    information_status = "abstract_available"
                    extraction_version = extraction_version or "source-metadata-v1"
                article_types = [str(value) for value in signals.get("article_types", [])]
                material_type = str(record.metadata.get("material_type", ""))
                results.append(
                    TriageInput(
                        material_ref=row["material_id"],
                        source_record_id=row["source_record_id"],
                        source_id=record.source_id,
                        title=record.title,
                        journal=journal or str(record.metadata.get("journal", "")),
                        url=record.url,
                        fields=fields,
                        categories=[str(value) for value in signals.get("categories", [])],
                        article_types=article_types or ([material_type] if material_type else []),
                        information_status=information_status,
                        extraction_version=extraction_version,
                    )
                )
        return results

    def cached(self, identity: str) -> TriageDecision | None:
        """错误不进缓存；成功模型输出按完整版本身份复用。"""
        with transaction(self.root) as db:
            row = db.execute("SELECT payload FROM rss_triage_decisions WHERE id=?", (identity,)).fetchone()
        return TriageDecision.model_validate_json(row[0]) if row else None

    def finish(self, batch_id: str, item: TriageInput, decision: TriageDecision) -> None:
        """结果、批次关联和后续状态在同一事务提交，重复提交保持幂等。"""
        if decision.input_hash != item.content_hash():
            raise ValueError("预选结果不属于当前输入版本")
        with transaction(self.root) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id,assessment,state FROM discovery_batch_items "
                "WHERE batch_id=? AND source_record_id=?",
                (batch_id, item.source_record_id),
            ).fetchone()
            if row is None or row["state"] != "awaiting_triage":
                return
            db.execute(
                "INSERT OR IGNORE INTO rss_triage_decisions(id,payload) VALUES (?,?)",
                (decision.id, decision.model_dump_json()),
            )
            db.execute(
                "INSERT OR IGNORE INTO rss_triage_links(batch_id,source_record_id,decision_id) "
                "VALUES (?,?,?)",
                (batch_id, item.source_record_id, decision.id),
            )
            assessment = json.loads(row["assessment"])
            assessment.update(
                local_triage=decision.id,
                local_route=decision.route,
                reason=decision.routing_reason,
                # 只有标题或 Feed 简介时强制原文复判；原生摘要本身是可引用证据。
                require_primary_read=item.information_status != "abstract_available",
            )
            db.execute(
                "UPDATE discovery_batch_items SET state=?,assessment=?,assessment_revision="
                "assessment_revision+1 WHERE id=?",
                (
                    "not_selected"
                    if decision.route == "exclude"
                    else "needs_triage_evidence"
                    if decision.route == "needs_evidence"
                    else "awaiting_intelligence",
                    dump(assessment),
                    row["id"],
                ),
            )
            db.execute(
                "DELETE FROM rss_triage_errors WHERE batch_id=? AND source_record_id=?",
                (batch_id, item.source_record_id),
            )

    def fail(self, batch_id: str, item: TriageInput, error: str) -> None:
        """失败单独持久化，不改资料入选状态。"""
        with transaction(self.root) as db:
            db.execute(
                "INSERT INTO rss_triage_errors(batch_id,source_record_id,error,updated_at) "
                "VALUES (?,?,?,?) ON CONFLICT(batch_id,source_record_id) DO UPDATE SET "
                "error=excluded.error,updated_at=excluded.updated_at",
                (batch_id, item.source_record_id, error, utcnow()),
            )

    def decisions(self, batch_id: str) -> list[TriageDecision]:
        """返回批次可审阅的真实输入、概率及路由。"""
        with transaction(self.root) as db:
            rows = db.execute(
                "SELECT DISTINCT d.payload FROM rss_triage_decisions d "
                "JOIN rss_triage_links l ON l.decision_id=d.id WHERE l.batch_id=? "
                "ORDER BY d.id",
                (batch_id,),
            ).fetchall()
        return [TriageDecision.model_validate_json(row[0]) for row in rows]

    def batches(self, batch_id: str = "") -> list[dict[str, JsonValue]]:
        """返回最新 50 个发现批次及准确指定的历史批次；没有成员时状态计数为空。

        批次存在性不依赖预选是否已开始；没有判别记录也不代表已执行预选。
        """
        with transaction(self.root) as db:
            rows = db.execute(
                "WITH selected AS (SELECT id,rowid AS position FROM discovery_batches "
                "WHERE id IN (SELECT id FROM discovery_batches ORDER BY rowid DESC LIMIT 50) OR id=?) "
                "SELECT b.id AS batch_id,i.state,count(i.id) AS count FROM selected b "
                "LEFT JOIN discovery_batch_items i ON i.batch_id=b.id "
                "GROUP BY b.id,i.state ORDER BY b.position DESC,i.state",
                (batch_id,),
            ).fetchall()
        results = {}
        for row in rows:
            states = results.setdefault(row["batch_id"], {})
            if row["state"] is not None:
                states[row["state"]] = row["count"]
        return [{"batch_id": key, "states": value} for key, value in results.items()]
