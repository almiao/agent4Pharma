"""建议与人工确认的短事务协调；模型不具备调用确认接口的工具。"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from agent4pharma.infrastructure.persistence.sqlite.investigation_evidence import EvidenceRepository
from agent4pharma.infrastructure.persistence.sqlite.investigations import InvestigationRepository
from agent4pharma.infrastructure.persistence.sqlite.management_policies import PolicyRepository
from agent4pharma.infrastructure.persistence.sqlite.management_sources import ManagedSourceRepository
from agent4pharma.infrastructure.persistence.sqlite.venue_maintenance import watch_venue
from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction
from agent4pharma.modules.maintenance.change_contracts import ChangeProposal, MaintenanceChange
from agent4pharma.modules.maintenance.identifiers import normalized_url
from agent4pharma.shared.clock import utcnow
from agent4pharma.shared.serialization import dump
from agent4pharma.shared.url_validation import public_reference_url


def _proposal(row) -> ChangeProposal:
    return ChangeProposal(
        id=row["id"],
        job_id=row["job_id"],
        status=row["status"],
        change=json.loads(row["payload"]),
        resolution=row["resolution"] or "",
        created_at=row["created_at"],
    )


@dataclass
class MaintenanceChangeRepository:
    """建议留档及确认工作单元，跨模块参与者复用同一连接，不各自提交。"""

    root: Path

    def propose(self, job_id: str, change: MaintenanceChange) -> ChangeProposal:
        """校验绑定任务证据、URL 与基线，幂等保存待审建议。"""
        job = InvestigationRepository(self.root).get_job(job_id)
        if job.status != "running" or job.request.options.mode != "agent":
            raise ValueError("只有运行中的来源维护 Agent 可以提出建议")
        pages = {
            p.id: p
            for p in EvidenceRepository(self.root).list_evidence(job_id)
            if p.status in {"fetched", "manual_input"}
        }
        if set(change.evidence_ids) - pages.keys():
            raise ValueError("变更建议引用了未读取或其他任务的证据")
        selected = [pages[key] for key in change.evidence_ids]
        allowed = {
            normalized_url(u)
            for p in selected
            for u in [p.url, p.canonical_url, *[link.url for link in p.links]]
            if u
        }
        allowed |= {
            urlsplit(p.url).scheme + "://" + urlsplit(p.url).netloc
            for p in selected
            if urlsplit(p.url).scheme in {"http", "https"}
        }
        urls = (
            [change.venue_url]
            if change.kind == "venue_watch"
            else ([change.source.homepage_url, change.source.endpoint_url] if change.source else [])
        )
        for url in filter(None, urls):
            public_reference_url(url)
            if normalized_url(url) not in allowed:
                raise ValueError("建议 URL 未出现在实际证据中")
        if change.kind == "venue_watch" and not any(
            change.venue_name.casefold() in (p.venue_name + " " + p.text).casefold() for p in selected
        ):
            raise ValueError("期刊名称缺少页面证据")
        if change.kind == "source_follow" and change.source:
            change.source.status = "watch"
        if change.kind == "coarse_policy":
            change.expected_version = change.expected_version or job.request.policy.id
        if change.kind == "intelligence_policy":
            raise ValueError("旧情报准则变更已退役")
        payload = dump(change.model_dump(mode="json"))
        identity = "proposal-" + hashlib.sha256((job_id + payload).encode()).hexdigest()[:32]
        with transaction(self.root) as db:
            db.execute(
                "INSERT OR IGNORE INTO maintenance_proposals(id,job_id,status,payload,created_at) "
                "VALUES (?,?,'pending',?,?)",
                (identity, job_id, payload, utcnow()),
            )
        return next(p for p in self.list_proposals(job_id) if p.id == identity)

    def list_proposals(self, job_id: str | None = None) -> list[ChangeProposal]:
        """返回近期建议，保留已确认/拒绝的历史。"""
        with transaction(self.root) as db:
            rows = db.execute(
                "SELECT * FROM maintenance_proposals "
                + ("WHERE job_id=? " if job_id else "")
                + "ORDER BY created_at DESC LIMIT 200",
                (job_id,) if job_id else (),
            ).fetchall()
        return [_proposal(r) for r in rows]

    def resolve(self, proposal_id: str, accept: bool) -> ChangeProposal:
        """人工确认后原子应用；版本冲突或能力校验失败整体回滚。"""
        with transaction(self.root) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM maintenance_proposals WHERE id=?", (proposal_id,)).fetchone()
            if row is None:
                raise KeyError("建议不存在")
            proposal = _proposal(row)
            if proposal.status != "pending":
                return proposal
            status = db.execute("SELECT status FROM jobs WHERE id=?", (proposal.job_id,)).fetchone()[0]
            if status in {"queued", "running"}:
                raise ValueError("调查尚未结束，请稍后审阅完整建议")
            resolution = self._apply(db, proposal) if accept else "人工拒绝，未修改配置"
            db.execute(
                "UPDATE maintenance_proposals SET status=?,resolution=? WHERE id=?",
                ("accepted" if accept else "rejected", resolution, proposal_id),
            )
            db.execute(
                "INSERT INTO maintenance_audit(kind,entity_id,action,detail,created_at) VALUES (?,?,?,?,?)",
                ("proposal", proposal_id, "accept" if accept else "reject", resolution, utcnow()),
            )
            proposal.status, proposal.resolution = ("accepted" if accept else "rejected"), resolution
        return proposal

    def _apply(self, db, proposal: ChangeProposal) -> str:
        change = proposal.change
        if change.kind == "source_follow":
            sources = ManagedSourceRepository(self.root, db)
            source = sources.save_source(change.source, change.target_id or None)
            sources.promote_followed_source(source.id, proposal.job_id)
            return "已正式关注来源 " + source.id + "；仅已验证 RSS 入口可自动采集，其他入口待接入"
        if change.kind == "venue_watch":
            identity = watch_venue(
                db, change.venue_name, change.venue_url, change.target_id, change.expected_version
            )
            return "已关注期刊 " + identity + "；登记不等于具备专用采集连接器"
        if change.kind == "coarse_policy":
            policies = PolicyRepository(self.root, db)
            if policies.get_policy().id != change.expected_version:
                raise ValueError("粗筛版本已变化，请重新复测建议")
            version = policies.save_policy(change.coarse)
            policies.publish_policy(version.id)
            return "已发布粗筛版本 " + version.id + "，新运行生效，旧批次快照保留"
        raise ValueError("旧情报准则变更已退役")
