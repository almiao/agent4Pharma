"""制作业务的专用事务仓储；只在应用工作单元内写入，不各自提交。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from pydantic import BaseModel

from agent4pharma.modules.content.draft_contracts import DraftRevision, ResearchEvidence
from agent4pharma.modules.editorial.commission_contracts import (
    EditorialDecision,
    EditorialDecisionRecord,
    Story,
)
from agent4pharma.modules.publishing.human_approval import HumanApproval
from agent4pharma.modules.publishing.release_contracts import ChannelPackage, ReleaseReceipt
from agent4pharma.modules.publishing.wechat_delivery import WechatDeliveryRecord
from agent4pharma.modules.review.reader_review_contracts import ReaderReviewReport
from agent4pharma.modules.review.review_contracts import ReviewReport
from agent4pharma.runtime.production_execution import ProductionTask


def _one[T: BaseModel](connection: sqlite3.Connection, table: str, identity: str, model: type[T]) -> T:
    # 表名只来自下方仓储固定常量，调用方不能传任意标识符。
    row = connection.execute(f"SELECT payload FROM {table} WHERE id=?", (identity,)).fetchone()
    if row is None:
        raise KeyError("制作记录不存在")
    return model.model_validate_json(row[0])


def _list[T: BaseModel](connection: sqlite3.Connection, table: str, model: type[T]) -> list[T]:
    return [
        model.model_validate_json(r[0])
        for r in connection.execute(f"SELECT payload FROM {table} ORDER BY rowid")
    ]


def _read_story(payload: str) -> Story:
    story = json.loads(payload)
    brief = story["brief"]
    if story["status"] == "completed" and "question" in brief:
        # 已交付归档只投影当前界面所需字段，准确批准包和原任务书仍保存在原记录。
        story["brief"] = {field: brief[field] for field in ("angle", "title")}
        story["read_only"] = True
    return Story.model_validate(story)


@dataclass
class CommissionRepository:
    """editorial 拥有任务书与决定；立项和任务排队在同一写工作单元内执行。"""

    connection: sqlite3.Connection

    def get(self, identity: str) -> Story:
        """读取当前选题聚合。"""
        row = self.connection.execute(
            "SELECT payload FROM production_stories WHERE id=?", (identity,)
        ).fetchone()
        if row is None:
            raise KeyError("制作记录不存在")
        return _read_story(row[0])

    def list(self) -> list[Story]:
        """读取选题清单，生产台按阶段和状态展示。"""
        return [
            _read_story(row[0])
            for row in self.connection.execute("SELECT payload FROM production_stories ORDER BY rowid")
        ]

    def save(self, story: Story, expected_revision: int) -> None:
        """创建或乐观更新；任务书版本另行追加保存。"""
        if expected_revision == 0:
            self.connection.execute(
                "INSERT INTO production_stories(id,revision,payload) VALUES (?,?,?)",
                (story.id, story.revision, story.model_dump_json()),
            )
        else:
            changed = self.connection.execute(
                "UPDATE production_stories SET revision=?,payload=? WHERE id=? AND revision=? "
                "AND json_type(payload, '$.brief.question') IS NULL",
                (story.revision, story.model_dump_json(), story.id, expected_revision),
            )
            if changed.rowcount != 1:
                raise ValueError("选题版本冲突或属于旧归档任务书")
        self.connection.execute(
            "INSERT OR IGNORE INTO commission_versions(story_id,version,payload) VALUES (?,?,?)",
            (story.id, story.brief_version, story.brief.model_dump_json()),
        )

    def decision(self, identity: str, decision: EditorialDecision) -> None:
        """追加主编决定，保留延期与拒绝理由。"""
        self.connection.execute(
            "INSERT INTO editorial_decisions(id,payload) VALUES (?,?)", (identity, decision.model_dump_json())
        )

    def decisions(self) -> list[EditorialDecisionRecord]:
        """返回编辑决定；保留延期/拒绝/补证原因和重新查看条件。"""
        return [
            EditorialDecisionRecord(task_id=row[0], decision=EditorialDecision.model_validate_json(row[1]))
            for row in self.connection.execute("SELECT id,payload FROM editorial_decisions ORDER BY rowid")
        ]


@dataclass
class DraftRepository:
    """content 拥有稿件及选题可见证据；文本本地存储便于原子提交。"""

    connection: sqlite3.Connection

    def get(self, identity: str) -> DraftRevision:
        """读取不可变稿件。"""
        return _one(self.connection, "content_drafts", identity, DraftRevision)

    def list(self) -> list[DraftRevision]:
        """读取稿件版本。"""
        return _list(self.connection, "content_drafts", DraftRevision)

    def save(self, draft: DraftRevision) -> None:
        """追加稿件版本，拒绝覆盖旧稿。"""
        self.connection.execute(
            "INSERT INTO content_drafts(id,story_id,number,payload) VALUES (?,?,?,?)",
            (draft.id, draft.story_id, draft.number, draft.model_dump_json()),
        )

    def evidence(self, story_id: str) -> list[ResearchEvidence]:
        """读取选题授权的实际资料。"""
        return [
            ResearchEvidence.model_validate_json(r[0])
            for r in self.connection.execute(
                "SELECT payload FROM content_evidence WHERE story_id=? ORDER BY rowid", (story_id,)
            )
        ]

    def add_evidence(self, story_id: str, evidence: ResearchEvidence) -> None:
        """同 ID 同内容幂等，变更内容拒绝，要求新建证据版本。"""
        old = self.connection.execute(
            "SELECT payload FROM content_evidence WHERE story_id=? AND evidence_id=?", (story_id, evidence.id)
        ).fetchone()
        if old and ResearchEvidence.model_validate_json(old[0]) != evidence:
            raise ValueError("证据 ID 已绑定其他内容，请创建新版本")
        self.connection.execute(
            "INSERT OR IGNORE INTO content_evidence(story_id,evidence_id,payload) VALUES (?,?,?)",
            (story_id, evidence.id, evidence.model_dump_json()),
        )


@dataclass
class ReviewRepository:
    """review 拥有不可变核验报告，不更新作者稿件。"""

    connection: sqlite3.Connection

    def list(self) -> list[ReviewReport]:
        """读取历史审核。"""
        return _list(self.connection, "editorial_reviews", ReviewReport)

    def save(self, report: ReviewReport) -> None:
        """保存绑定准确稿件的审核。"""
        self.connection.execute(
            "INSERT INTO editorial_reviews(id,story_id,draft_id,payload) VALUES (?,?,?,?)",
            (report.id, report.story_id, report.draft_id, report.model_dump_json()),
        )

    def reader_list(self) -> list[ReaderReviewReport]:
        """读取真实阅读报告，不从旧事实审核推断。"""
        return _list(self.connection, "reader_reviews", ReaderReviewReport)

    def save_reader(self, report: ReaderReviewReport) -> None:
        """同一工作单元追加阅读报告，不独立提交。"""
        self.connection.execute(
            "INSERT INTO reader_reviews(id,story_id,draft_id,payload) VALUES (?,?,?,?)",
            (report.id, report.story_id, report.draft_id, report.model_dump_json()),
        )


@dataclass
class ReleaseRepository:
    """publishing 拥有渠道包和本地/外部回执，不把导出标为远端发布。"""

    connection: sqlite3.Connection

    def wechat_deliveries(self) -> list[WechatDeliveryRecord]:
        """读取草稿交付历史，不将未知结果当作可重试失败。"""
        return _list(self.connection, "wechat_deliveries", WechatDeliveryRecord)

    def save_wechat_delivery(self, record: WechatDeliveryRecord) -> None:
        """由同一工作单元保存交付状态；请求身份始终保持不变。"""
        self.connection.execute(
            "INSERT INTO wechat_deliveries(id,package_id,account_id,payload) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (record.id, record.request.package_id, record.request.account_id, record.model_dump_json()),
        )

    def approvals(self) -> list[HumanApproval]:
        """读取已确认包的准确指纹。"""
        return _list(self.connection, "human_package_approvals", HumanApproval)

    def approve(self, approval: HumanApproval) -> None:
        """同包指纹幂等保存，事务由工作单元提交。"""
        self.connection.execute(
            "INSERT OR IGNORE INTO human_package_approvals(id,package_id,fingerprint,payload) "
            "VALUES (?,?,?,?)",
            (approval.id, approval.package_id, approval.fingerprint, approval.model_dump_json()),
        )

    def packages(self) -> list[ChannelPackage]:
        """读取所有渠道包。"""
        return _list(self.connection, "channel_packages", ChannelPackage)

    def receipts(self) -> list[ReleaseReceipt]:
        """读取真实发行回执。"""
        return _list(self.connection, "release_receipts", ReleaseReceipt)

    def save(self, package: ChannelPackage) -> None:
        """保存不可变预览产物。"""
        self.connection.execute(
            "INSERT INTO channel_packages(id,story_id,draft_id,payload) VALUES (?,?,?,?)",
            (package.id, package.story_id, package.draft_id, package.model_dump_json()),
        )

    def receipt(self, receipt: ReleaseReceipt) -> None:
        """记录回执，同操作 ID 不重复落账。"""
        self.connection.execute(
            "INSERT OR IGNORE INTO release_receipts(id,package_id,payload) VALUES (?,?,?)",
            (receipt.id, receipt.package_id, receipt.model_dump_json()),
        )


@dataclass
class ProductionTaskRepository:
    """runtime 制作任务租约与状态持久化；所有更新由持有工作单元的程序校验。"""

    connection: sqlite3.Connection

    def get(self, identity: str) -> ProductionTask:
        """读取任务。"""
        return _one(self.connection, "production_tasks", identity, ProductionTask)

    def list(self) -> list[ProductionTask]:
        """读取恢复队列及历史尝试。"""
        return _list(self.connection, "production_tasks", ProductionTask)

    def save(self, task: ProductionTask) -> None:
        """原子工作单元内插入或更新，业务输出与任务完成一并提交。"""
        self.connection.execute(
            "INSERT INTO production_tasks(id,role,story_id,status,payload) VALUES (?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status,payload=excluded.payload,"
            "revision=revision+1",
            (task.id, task.role, task.story_id, task.status, task.model_dump_json()),
        )

    def event(self, identity: str, story_id: str, actor: str, action: str, reason: str, now: str) -> None:
        """保存程序/人工操作原因，不包含密钥或模型诊断原文。"""
        self.connection.execute(
            "INSERT INTO production_operator_events(id,story_id,actor_id,action,reason,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (identity, story_id, actor, action, reason, now),
        )
