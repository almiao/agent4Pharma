"""历史情报标准、评估及候选的只读仓储，使用独立短连接。"""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue

from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction
from agent4pharma.modules.discovery.evidence_status import evidence_status
from agent4pharma.modules.discovery.intelligence_contracts import (
    IntelligenceEvidence,
    IntelligenceMaintenanceRequest,
    IntelligencePolicyVersion,
    IntelligenceTask,
    IntelligenceTaskSummary,
)


def _version(row) -> IntelligencePolicyVersion:
    return IntelligencePolicyVersion(
        id=row["id"],
        version=row["version"],
        status=row["status"],
        definition=json.loads(row["definition"]),
        content_hash=row["content_hash"],
    )


@dataclass
class IntelligenceRepository:
    """仅查询历史判断、证据与候选，不创建任务、发布准则或修改结果。"""

    root: Path
    connection: sqlite3.Connection | None = None

    def versions(self) -> list[IntelligencePolicyVersion]:
        """列出语义标准全部版本。"""
        with transaction(self.root, self.connection) as db:
            return [
                _version(r) for r in db.execute("SELECT * FROM intelligence_policies ORDER BY version DESC")
            ]

    def policy(self, policy_id: str | None = None) -> IntelligencePolicyVersion:
        """取得指定或已发布版本，尚未初始化明确失败。"""
        with transaction(self.root, self.connection) as db:
            row = (
                db.execute("SELECT * FROM intelligence_policies WHERE id=?", (policy_id,)).fetchone()
                if policy_id
                else db.execute("SELECT * FROM intelligence_policies WHERE status='published'").fetchone()
            )
        if row is None:
            raise KeyError("情报准则不存在或尚未发布")
        return _version(row)

    def get(self, task_id: str) -> IntelligenceTask:
        """读取绑定评估快照。"""
        with transaction(self.root, self.connection) as db:
            row = db.execute("SELECT * FROM intelligence_tasks WHERE id=?", (task_id,)).fetchone()
            readings = [
                IntelligenceEvidence.model_validate_json(r[0])
                for r in db.execute("SELECT payload FROM intelligence_readings WHERE task_id=?", (task_id,))
            ]
        if row is None:
            raise KeyError("情报任务不存在")
        return IntelligenceTask(
            id=row["id"],
            status=row["status"],
            request=json.loads(row["request"]),
            result=json.loads(row["result"]) if row["result"] else None,
            error=row["error"],
            supplemental_evidence=readings,
        )

    def candidates(self, parent_ref: str = "") -> list[dict[str, JsonValue]]:
        """读取主编待选池，附任务快照可审阅。"""
        with transaction(self.root, self.connection) as db:
            rows = db.execute(
                "SELECT c.* FROM editorial_candidates c JOIN intelligence_tasks t ON t.id=c.task_id "
                "WHERE c.status='recommend' "
                + (
                    "AND (json_extract(t.request,'$.parent_ref')=? OR EXISTS (SELECT 1 "
                    "FROM discovery_batch_items i WHERE i.batch_id=? "
                    "AND json_extract(i.assessment,'$.intelligence_task')=t.id "
                    "AND i.material_id=json_extract(t.request,'$.material_ref') "
                    "AND i.source_record_id=json_extract(t.request,'$.evidence[0].id'))) "
                    if parent_ref
                    else ""
                )
                + "ORDER BY c.created_at DESC"
                + ("" if parent_ref else " LIMIT 200"),
                (parent_ref, parent_ref) if parent_ref else (),
            ).fetchall()
        return [{**dict(r), "card": json.loads(r["card"])} for r in rows]

    def maintenance_requests(self) -> list[IntelligenceMaintenanceRequest]:
        """维护请求可人工启动来源调查，避免每条日常材料重复调用两个角色。"""
        with transaction(self.root, self.connection) as db:
            rows = db.execute(
                "SELECT r.*,coalesce(j.status,'pending') AS investigation_status "
                "FROM intelligence_maintenance_requests r LEFT JOIN jobs j "
                "ON j.id='investigate-followup-'||r.task_id ORDER BY r.created_at DESC LIMIT 100"
            ).fetchall()
        return [IntelligenceMaintenanceRequest(**dict(r)) for r in rows]

    def recent_summaries(self, limit: int = 100) -> list[IntelligenceTaskSummary]:
        """只投影列表字段，最多读取 200 个任务，避免一次加载历史全文。"""
        if not 1 <= limit <= 200:
            raise ValueError("情报列表额度必须在 1 到 200 之间")
        with transaction(self.root, self.connection) as db:
            rows = db.execute(
                "SELECT id,status,error,updated_at,"
                "json_extract(request,'$.material_ref') AS material_ref,"
                "coalesce(json_extract(request,'$.evidence[0].title'),'') AS title,"
                "json_extract(request,'$.mode') AS mode,"
                "coalesce(json_extract(request,'$.stage'),'legacy') AS stage,"
                "coalesce(json_extract(request,'$.parent_ref'),'') AS parent_ref,"
                "coalesce(json_extract(result,'$.summary'),'') AS summary,"
                "coalesce(json_extract(result,'$.disposition'),'') AS disposition "
                "FROM intelligence_tasks ORDER BY updated_at DESC,id LIMIT ?",
                (limit,),
            ).fetchall()
        return [IntelligenceTaskSummary(**dict(row)) for row in rows]

    def activity_summaries(self, since: str) -> list[IntelligenceTaskSummary]:
        """后台活动读取全部未终结记录及指定时间后的结果，不含模型输入正文。"""
        with transaction(self.root, self.connection) as db:
            rows = db.execute(
                "SELECT id,status,updated_at,"
                "json_extract(request,'$.material_ref') AS material_ref,"
                "coalesce(json_extract(request,'$.evidence[0].title'),'') AS title,"
                "json_extract(request,'$.mode') AS mode,"
                "coalesce(json_extract(request,'$.stage'),'legacy') AS stage,"
                "coalesce(json_extract(request,'$.selection_ref'),'') AS selection_ref,"
                "coalesce(json_extract(request,'$.parent_ref'),'') AS parent_ref "
                "FROM intelligence_tasks WHERE status IN ('pending','queued','running') OR updated_at>=? "
                "ORDER BY updated_at DESC,id",
                (since,),
            ).fetchall()
        return [IntelligenceTaskSummary(**dict(row)) for row in rows]

    def run_summaries(self, parent_ref: str) -> list[IntelligenceTaskSummary]:
        """读取本批原生或明确导入的评估，逐来源版本核对，保留评估原父批次身份。"""
        from agent4pharma.infrastructure.persistence.sqlite.intelligence_list_reading import (
            run_task_summaries,
        )

        with transaction(self.root, self.connection) as db:
            summaries = run_task_summaries(db, parent_ref)
        latest = {}
        for summary in summaries:
            key = (summary.source_record_id, summary.mode, summary.stage)
            if key not in latest or (summary.updated_at, summary.id) > (
                latest[key].updated_at,
                latest[key].id,
            ):
                latest[key] = summary
        for summary in summaries:
            summary.current = latest[(summary.source_record_id, summary.mode, summary.stage)].id == summary.id
        return summaries

    def summaries_for_ids(self, task_ids: list[str]) -> list[IntelligenceTaskSummary]:
        """读取最多 200 个明确任务；不通过最近列表回推历史选题输入。"""
        if len(task_ids) > 200 or any(len(identity) > 200 for identity in task_ids):
            raise ValueError("明确任务引用最多 200 项，每项不超过 200 字符")
        summaries = []
        for identity in dict.fromkeys(task_ids):
            task = self.get(identity)
            abstract, fulltext = evidence_status(task)
            summaries.append(
                IntelligenceTaskSummary(
                    stage=task.request.stage,
                    id=task.id,
                    status=task.status,
                    material_ref=task.request.material_ref,
                    title=task.request.evidence[0].title,
                    mode=task.request.mode,
                    parent_ref=task.request.parent_ref,
                    updated_at="",
                    error=task.error,
                    summary=task.result.summary if task.result else "",
                    disposition=task.result.disposition if task.result else "",
                    source_record_id=task.request.evidence[0].id,
                    abstract_state=abstract,
                    fulltext_state=fulltext,
                )
            )
        return summaries

    def tasks_for_parent(self, parent_ref: str) -> list[IntelligenceTask]:
        """按不可变父任务查评估，禁止扫描其他调查用于自检。"""
        with transaction(self.root, self.connection) as db:
            identities = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM intelligence_tasks WHERE json_extract(request,'$.parent_ref')=?",
                    (parent_ref,),
                )
            ]
        return [self.get(identity) for identity in identities]

    def tasks_for_selection(self, selection_ref: str) -> list[IntelligenceTask]:
        """读取准确摘要运行的实时任务，不混入旧精筛、全文或其他运行。"""
        with transaction(self.root, self.connection) as db:
            identities = [
                row[0]
                for row in db.execute(
                    "SELECT id FROM intelligence_tasks WHERE json_extract(request,'$.selection_ref')=? "
                    "AND json_extract(request,'$.stage')='abstract' ORDER BY updated_at,id",
                    (selection_ref,),
                )
            ]
        return [self.get(identity) for identity in identities]
