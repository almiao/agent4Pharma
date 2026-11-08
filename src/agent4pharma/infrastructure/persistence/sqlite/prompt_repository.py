"""专用提示词版本仓储；共用主题标准与预选正文共享发布事务。"""

import hashlib
import sqlite3
import uuid
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction
from agent4pharma.modules.model_configuration.prompt_contracts import (
    PromptDraft,
    PromptKey,
    PromptPublication,
    PromptVersion,
)
from agent4pharma.modules.model_configuration.screening_prompts import (
    SCOPE_PREFIX,
    STAGE_SEPARATOR,
    TRIAGE_STAGE,
    compose_screening_prompt,
    split_screening_prompt,
)
from agent4pharma.shared.clock import utcnow


class PromptRepository:
    """每次操作独立短连接；默认版本只读，编辑和发布不调用模型。"""

    def __init__(
        self, root: Path, defaults: dict[PromptKey, str], connection: sqlite3.Connection | None = None
    ):
        self.root, self.defaults = root, defaults
        self.connection = connection

    def versions(self, key: PromptKey) -> list[PromptVersion]:
        """返回指定节点历史；无人工发布时提供随包默认版本。"""
        if key not in self.defaults:
            raise KeyError("提示词节点不存在")
        with transaction(self.root, self.connection) as db:
            rows = db.execute(
                "SELECT * FROM prompt_versions WHERE node_key=? ORDER BY version DESC", (key,)
            ).fetchall()
        values = [
            PromptVersion.model_validate({**dict(r), "key": r["node_key"]}, extra="ignore") for r in rows
        ]
        baseline = self.defaults[key]
        digest = hashlib.sha256(baseline.encode()).hexdigest()
        values.append(
            PromptVersion(
                id=f"builtin-{key}-{digest[:16]}",
                key=key,
                version=0,
                status="archived" if any(p.status == "published" for p in values) else "builtin",
                instructions=baseline,
                content_hash=digest,
            )
        )
        return values

    def current(self, key: PromptKey) -> PromptVersion:
        """读取新任务采用的版本，旧任务应使用自身快照。"""
        return next(p for p in self.versions(key) if p.status in {"published", "builtin"})

    def catalog(self, keys: list[PromptKey]) -> dict[PromptKey, list[PromptVersion]]:
        """在同一个读快照取得目录，避免并发配对发布造成界面混合版本。"""
        with transaction(self.root, self.connection) as db:
            if not db.in_transaction:
                db.execute("BEGIN")
            participant = PromptRepository(self.root, self.defaults, db)
            return {key: participant.versions(key) for key in keys}

    def draft(self, key: PromptKey, value: PromptDraft) -> PromptVersion:
        """追加不可变草稿；版本号在写锁内分配。"""
        self.current(key)
        standard_text = value.instructions + ("\n\n参考示例：\n" + value.examples if value.examples else "")
        if key in {"audience", "scientific_evidence", "chinese_news_style"} and len(standard_text) > 23000:
            raise ValueError("公共规范正文与示例合计最多23000字符")
        if key == "screening_scope" and (
            SCOPE_PREFIX in value.instructions
            or STAGE_SEPARATOR.strip() in value.instructions
            or SCOPE_PREFIX in value.examples
            or STAGE_SEPARATOR.strip() in value.examples
        ):
            raise ValueError("共用标准不能包含系统保留的组合标记")
        if key == "triage":
            scope = self.current("screening_scope")
            if scope.status == "published":
                stage = split_screening_prompt(value.instructions).stage
                value = value.model_copy(
                    update={"instructions": compose_screening_prompt(scope.id, scope.text(), stage)}
                )
                value = PromptDraft.model_validate(value.model_dump())
        identity = "prompt-" + uuid.uuid4().hex
        with transaction(self.root, self.connection) as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            number = db.execute(
                "SELECT coalesce(max(version),0)+1 FROM prompt_versions WHERE node_key=?", (key,)
            ).fetchone()[0]
            db.execute(
                "INSERT INTO prompt_versions "
                "(id,node_key,version,status,instructions,examples,note,created_at,content_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    key,
                    number,
                    "draft",
                    value.instructions,
                    value.examples,
                    value.note,
                    utcnow(),
                    hashlib.sha256(value.model_dump_json(exclude={"note"}).encode()).hexdigest(),
                ),
            )
        return next(p for p in self.versions(key) if p.id == identity)

    def publish(self, key: PromptKey, request: PromptPublication) -> PromptVersion:
        """写锁内比较版本；共用标准与预选组合正文同事务发布。"""
        with transaction(self.root, self.connection) as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            participant = PromptRepository(self.root, self.defaults, db)
            participant._publish_locked(key, request)
            return participant.current(key)

    def _publish_locked(self, key: PromptKey, request: PromptPublication) -> None:
        current = self.current(key)
        target = next((v for v in self.versions(key) if v.id == request.version_id), None)
        if target is None:
            raise KeyError("草稿不存在于该节点")
        if current.id != request.expected_current:
            raise ValueError("当前版本已变更，请刷新并重新对比后发布")
        if target.status != "draft":
            raise ValueError("只能发布草稿；恢复历史版本请先另存为新草稿")
        if key == "screening_scope":
            self._publish_scope(target, request)
            return
        if key == "triage":
            scope = self.current("screening_scope")
            if scope.status == "published":
                parts = split_screening_prompt(target.instructions)
                expected = compose_screening_prompt(scope.id, scope.text(), parts.stage)
                if parts.scope_ref != scope.id or target.instructions != expected:
                    raise ValueError("草稿绑定的共用主题标准已过期，请另存新草稿再发布")
        self._activate(target)

    def _activate(self, target: PromptVersion) -> None:
        with transaction(self.root, self.connection) as db:
            db.execute(
                "UPDATE prompt_versions SET status='archived' WHERE node_key=? AND status='published'",
                (target.key,),
            )
            db.execute("UPDATE prompt_versions SET status='published' WHERE id=?", (target.id,))

    def _publish_scope(self, target: PromptVersion, request: PromptPublication) -> None:
        if set(request.expected_related) != {"triage"}:
            raise ValueError("发布共用标准必须确认预选的当前版本")
        stages = {key: self.current(key) for key in ("triage",)}
        if any(stages[key].id != expected for key, expected in request.expected_related.items()):
            raise ValueError("预选版本已变更，请刷新并重新对比后发布")
        self._activate(target)
        for key, old in stages.items():
            parts = split_screening_prompt(old.instructions)
            stage = parts.stage if parts.scope_ref else TRIAGE_STAGE
            draft = self.draft(
                key,
                PromptDraft(
                    instructions=stage,
                    examples=old.examples,
                ),
            )
            self._publish_locked(key, PromptPublication(version_id=draft.id, expected_current=old.id))
