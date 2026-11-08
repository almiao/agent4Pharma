"""调查页面证据持久化与内容寻址原件，失败结果也占用访问预算。"""

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.investigations import InvestigationRepository
from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction
from agent4pharma.modules.maintenance.models import PageEvidence
from agent4pharma.shared.serialization import dump


@dataclass
class EvidenceRepository:
    """每次任务的 URL 去重与页面额度，不允许 MCP 绕过调查预算。"""

    root: Path

    def list_evidence(self, job_id: str) -> list[PageEvidence]:
        """读取某任务所有页面尝试，不返回其他任务原件。"""
        with transaction(self.root) as db:
            return [
                PageEvidence.model_validate_json(r[0])
                for r in db.execute(
                    "SELECT payload FROM investigation_evidence WHERE job_id=? ORDER BY rowid", (job_id,)
                )
            ]

    def save_evidence(self, page: PageEvidence, body: bytes = b"") -> PageEvidence:
        """先留档文件再提交引用；超额或重复请求不会增加额外证据记录。"""
        job = InvestigationRepository(self.root).get_job(page.job_id)
        if body:
            digest = hashlib.sha256(body).hexdigest()
            folder = self.root / "workbench" / "evidence" / digest[:2]
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / digest
            if not target.exists():
                temporary = target.with_suffix("." + uuid.uuid4().hex + ".tmp")
                temporary.write_bytes(body)
                temporary.replace(target)
            page = page.model_copy(update={"content_hash": digest})
        with transaction(self.root) as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT payload FROM investigation_evidence WHERE job_id=? AND requested_url=?",
                (page.job_id, page.requested_url),
            ).fetchone()
            if old:
                previous = PageEvidence.model_validate_json(old[0])
                if previous.id == page.id and previous.status == "fetching" and page.status != "fetching":
                    db.execute(
                        "UPDATE investigation_evidence SET payload=? WHERE id=?",
                        (dump(page.model_dump(mode="json")), page.id),
                    )
                    return page
                return previous
            count = db.execute(
                "SELECT count(*) FROM investigation_evidence WHERE job_id=? "
                "AND json_extract(payload,'$.status')!='manual_input'",
                (page.job_id,),
            ).fetchone()[0]
            if page.status != "manual_input" and count >= job.request.options.max_pages:
                raise ValueError("已达到本次调查页面预算")
            db.execute(
                "INSERT INTO investigation_evidence(id,job_id,requested_url,payload) VALUES (?,?,?,?)",
                (page.id, page.job_id, page.requested_url, dump(page.model_dump(mode="json"))),
            )
        return page
