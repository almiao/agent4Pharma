"""复测词表版本存取；发布供新运行使用，旧批次保留快照。"""

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction
from agent4pharma.modules.discovery.screening_policy import PolicyVersion, ScreeningPolicy
from agent4pharma.shared.clock import utcnow
from agent4pharma.shared.serialization import dump


def _version(row) -> PolicyVersion:
    return PolicyVersion(**{**dict(row), "definition": json.loads(row["definition"])})


@dataclass
class PolicyRepository:
    """策略版本追加保存，发布时原子替换唯一已发布版本。"""

    root: Path
    connection: sqlite3.Connection | None = None

    def ensure_default(self, definition: ScreeningPolicy) -> None:
        """启动时原子初始化默认发布版，不把普通草案默认为生产配置。"""
        with transaction(self.root, self.connection) as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            participant = PolicyRepository(self.root, db)
            if not any(p.status == "published" for p in participant.list_versions()):
                participant.publish_policy(participant.save_policy(definition).id)

    def list_versions(self) -> list[PolicyVersion]:
        """返回所有版本，不隐藏已被替代的策略。"""
        with transaction(self.root, self.connection) as db:
            return [
                _version(r)
                for r in db.execute("SELECT * FROM screening_policy_versions ORDER BY version DESC")
            ]

    def get_policy(self, policy_id: str | None = None) -> PolicyVersion:
        """取得指定版本或当前发布版；无发布版时使用最新草案并保留其标识。"""
        with transaction(self.root, self.connection) as db:
            row = (
                db.execute("SELECT * FROM screening_policy_versions WHERE id=?", (policy_id,)).fetchone()
                if policy_id
                else db.execute(
                    "SELECT * FROM screening_policy_versions "
                    "ORDER BY (status='published') DESC,version DESC LIMIT 1"
                ).fetchone()
            )
        if row is None:
            raise KeyError("策略不存在")
        return _version(row)

    def save_policy(self, definition: ScreeningPolicy) -> PolicyVersion:
        """保存新草案，不就地改写旧版本，重复内容也保留本次编辑记录。"""
        payload = dump(definition.model_dump())
        policy_id, now = "policy-" + uuid.uuid4().hex, utcnow()
        with transaction(self.root, self.connection) as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            version = db.execute(
                "SELECT coalesce(max(version),0)+1 FROM screening_policy_versions"
            ).fetchone()[0]
            db.execute(
                "INSERT INTO screening_policy_versions(id,version,status,definition,content_hash,created_at) "
                "VALUES (?,?,'draft',?,?,?)",
                (policy_id, version, payload, hashlib.sha256(payload.encode()).hexdigest(), now),
            )
            db.execute(
                "INSERT INTO maintenance_audit(kind,entity_id,action,detail,created_at) VALUES (?,?,?,?,?)",
                ("policy", policy_id, "create_draft", payload, now),
            )
        return self.get_policy(policy_id)

    def publish_policy(self, policy_id: str) -> PolicyVersion:
        """原子发布粗筛策略；后续新运行采用，旧快照不变。"""
        with transaction(self.root, self.connection) as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            if (
                db.execute("SELECT 1 FROM screening_policy_versions WHERE id=?", (policy_id,)).fetchone()
                is None
            ):
                raise KeyError("策略不存在")
            db.execute("UPDATE screening_policy_versions SET status='archived' WHERE status='published'")
            db.execute("UPDATE screening_policy_versions SET status='published' WHERE id=?", (policy_id,))
            db.execute(
                "INSERT INTO maintenance_audit(kind,entity_id,action,detail,created_at) VALUES (?,?,?,?,?)",
                ("policy", policy_id, "publish_screening_policy", "{}", utcnow()),
            )
        return self.get_policy(policy_id)
