"""保存有时间与来源身份的实际补充复核，不更改原采集错误、游标和完成状态。"""

import json
from dataclasses import dataclass
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction
from agent4pharma.modules.collection.acquisition_interfaces import CollectionDiagnostic
from agent4pharma.shared.clock import utcnow
from agent4pharma.shared.serialization import dump


@dataclass
class CollectionDiagnosticsRepository:
    """批量复核以一个工作单元保存；只接受该批次失败/部分失败来源的实际复核。"""

    root: Path

    def save(self, batch_id: str, diagnostics: list[CollectionDiagnostic]) -> None:
        """校验来源属于批次且仍失败；写补充诊断及审计，不发起网络或推断原 HTTP 状态。"""
        with transaction(self.root) as db:
            db.execute("BEGIN IMMEDIATE")
            for diagnostic in diagnostics:
                job = db.execute(
                    "SELECT id,status,result FROM jobs WHERE parent_id=? AND kind='collect' "
                    "AND json_extract(request,'$.source')=?",
                    (batch_id, diagnostic.source_id),
                ).fetchone()
                if job is None or job["status"] not in {"failed", "partial"}:
                    raise ValueError("复核来源不属于此批次的失败任务")
                result = json.loads(job["result"] or "{}")
                result["diagnostic"] = diagnostic.model_dump(mode="json")
                db.execute("UPDATE jobs SET result=? WHERE id=?", (dump(result), job["id"]))
                db.execute(
                    "INSERT INTO maintenance_audit(kind,entity_id,action,detail,created_at) "
                    "VALUES (?,?,?,?,?)",
                    ("collection", job["id"], "failure_recheck", diagnostic.model_dump_json(), utcnow()),
                )
