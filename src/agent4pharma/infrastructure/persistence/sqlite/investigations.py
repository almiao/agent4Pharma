"""人工样本与调查任务存取；任务复用已有 jobs，所有连接按操作释放。"""

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction
from agent4pharma.modules.maintenance.models import (
    ArticleCase,
    ArticleCaseInput,
    InvestigationJob,
    InvestigationReport,
    InvestigationRequest,
)
from agent4pharma.shared.clock import utcnow
from agent4pharma.shared.serialization import dump


def _case(row) -> ArticleCase:
    return ArticleCase(
        **{
            **json.loads(row["input_json"]),
            "id": row["id"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


def _job(row) -> InvestigationJob:
    return InvestigationJob(
        id=row["id"],
        status=row["status"],
        request=json.loads(row["request"]),
        result=json.loads(row["result"]) if row["result"] else None,
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@dataclass
class InvestigationRepository:
    """样本编辑、幂等任务创建与运行历史；网页原件存取由证据仓储负责。"""

    root: Path

    def list_cases(self) -> list[ArticleCase]:
        """返回最多 200 份最近人工样本。"""
        with transaction(self.root) as db:
            return [
                _case(r)
                for r in db.execute("SELECT * FROM validation_cases ORDER BY updated_at DESC LIMIT 200")
            ]

    def get_case(self, case_id: str) -> ArticleCase:
        """读取样本；不存在时抛出 KeyError。"""
        with transaction(self.root) as db:
            row = db.execute("SELECT * FROM validation_cases WHERE id=?", (case_id,)).fetchone()
        if row is None:
            raise KeyError("样本不存在")
        return _case(row)

    def save_case(self, change: ArticleCaseInput, case_id: str | None = None) -> ArticleCase:
        """乐观锁保存人工标注；已有调查请求仍保留旧输入快照。"""
        identity, now = case_id or "case-" + uuid.uuid4().hex, utcnow()
        if not change.reference.strip():
            raise ValueError("请输入文章链接、DOI、PMID、arXiv ID 或标题")
        with transaction(self.root) as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM validation_cases WHERE id=?", (identity,)).fetchone()
            if case_id and old is None:
                raise KeyError("样本不存在")
            if old and change.revision != old["revision"]:
                raise ValueError("样本已被修改，请刷新后重试")
            revision = old["revision"] + 1 if old else 1
            payload = dump(change.model_dump(exclude={"revision"}))
            db.execute(
                "INSERT INTO validation_cases(id,input_json,revision,created_at,updated_at) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET input_json=excluded.input_json,revision=excluded.revision,"
                "updated_at=excluded.updated_at",
                (identity, payload, revision, now, now),
            )
            db.execute(
                "INSERT INTO maintenance_audit(kind,entity_id,action,detail,created_at) VALUES (?,?,?,?,?)",
                ("case", identity, "update" if old else "create", payload, now),
            )
        return self.get_case(identity)

    def create_run(self, request: InvestigationRequest) -> InvestigationJob:
        """同一个 request_id 幂等；输入不一致的重复请求明确拒绝。"""
        identity = "investigate-" + (request.options.request_id or uuid.uuid4().hex)
        payload, now = dump(request.model_dump(mode="json")), utcnow()
        with transaction(self.root) as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT request FROM jobs WHERE id=?", (identity,)).fetchone()
            if old and InvestigationRequest.model_validate_json(old["request"]).model_dump(
                mode="json"
            ) != request.model_dump(mode="json"):
                raise ValueError("request_id 已用于另一请求，请重新发起")
            db.execute(
                "INSERT OR IGNORE INTO jobs(id,kind,logical_key,parent_id,attempt,status,request,"
                "result,error,updated_at) VALUES (?,?,?,NULL,0,'queued',?,NULL,NULL,?)",
                (identity, "source_investigation", identity, payload, now),
            )
            db.execute(
                "INSERT OR IGNORE INTO maintenance_runs(job_id,case_id,policy_id,created_at) "
                "VALUES (?,?,?,?)",
                (identity, request.case.id, request.policy.id, now),
            )
        return self.get_job(identity)

    def get_job(self, job_id: str) -> InvestigationJob:
        """读取调查任务，不能通过此接口读取其他业务任务。"""
        with transaction(self.root) as db:
            row = db.execute(
                "SELECT j.*,m.created_at FROM jobs j JOIN maintenance_runs m ON m.job_id=j.id WHERE j.id=?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise KeyError("调查任务不存在")
        return _job(row)

    def list_jobs(self) -> list[InvestigationJob]:
        """返回最近 100 次调查的独立快照。"""
        with transaction(self.root) as db:
            return [
                _job(r)
                for r in db.execute(
                    "SELECT j.*,m.created_at FROM jobs j JOIN maintenance_runs m ON m.job_id=j.id "
                    "ORDER BY m.created_at DESC LIMIT 100"
                )
            ]

    def update_job(
        self, job_id: str, status: str, report: InvestigationReport | None = None, error: str | None = None
    ) -> None:
        """保存结果和安全错误；已有页面证据独立保留。"""
        self.get_job(job_id)
        with transaction(self.root) as db:
            db.execute(
                "UPDATE jobs SET status=?,result=?,error=?,updated_at=?,"
                "attempt=attempt+CASE WHEN ?='running' AND status!='running' THEN 1 ELSE 0 END WHERE id=?",
                (
                    status,
                    dump(report.model_dump(mode="json")) if report else None,
                    error,
                    utcnow(),
                    status,
                    job_id,
                ),
            )

    def recover_interrupted(self) -> None:
        """本地工作台重启后显式标记未完成任务，避免把旧 running 永久显示为正常运行。"""
        with transaction(self.root) as db:
            db.execute(
                "UPDATE jobs SET status='interrupted',error=?,updated_at=? "
                "WHERE kind='source_investigation' AND status IN ('queued','running')",
                ("工作台重启，调查未完成；可重新运行", utcnow()),
            )
            db.execute(
                "UPDATE intelligence_tasks SET status='interrupted',error='维护任务中断，可重试' "
                "WHERE status='running' AND json_extract(request,'$.parent_ref') IN "
                "(SELECT id FROM jobs WHERE kind='source_investigation' AND status='interrupted')"
            )
