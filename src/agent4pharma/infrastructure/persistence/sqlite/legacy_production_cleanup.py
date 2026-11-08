"""维护用的旧孤立立项失败清理；显式任务身份、先备份、拒绝任何业务引用。"""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.production_repository import ProductionTaskRepository
from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction


@dataclass(frozen=True)
class LegacyTaskRemoval:
    """单条旧任务及其专属执行记录的实际清理结果。"""

    task_id: str
    tasks: int
    observations: int
    operator_events: int
    backup: Path


def _require_unreferenced(db: sqlite3.Connection, task_id: str) -> None:
    task = ProductionTaskRepository(db).get(task_id)
    if (
        task.role != "editor"
        or task.phase
        or task.status != "failed"
        or task.story_id
        or task.parent_task_id
        or task.result_ref
        or task.editing_result_json
    ):
        raise ValueError("只允许清理无阶段、无选题和产物的旧孤立立项失败任务")
    references = (
        "production_tasks",
        "production_stories",
        "editorial_decisions",
        "commission_versions",
        "content_drafts",
        "editorial_reviews",
        "channel_packages",
        "release_receipts",
        "article_regressions",
    )
    for table in references:
        query = f"SELECT 1 FROM {table} WHERE instr(payload, ?) > 0"
        params = (task_id,)
        if table == "production_tasks":
            query += " AND id <> ?"
            params = (task_id, task_id)
        if db.execute(query + " LIMIT 1", params).fetchone():
            raise ValueError(f"任务仍被 {table} 引用，停止清理")


def remove_legacy_production_task(root: Path, task_id: str, backup: Path) -> LegacyTaskRemoval:
    """维护入口显式传入任务ID；备份后原子重校验并删除，不删除候选、论文或共享资料。"""
    if backup.exists() or backup.resolve() == (root / "discovery.sqlite3").resolve():
        raise ValueError("备份目标必须是新的独立文件")
    with transaction(root) as db:
        if db.execute("SELECT 1 FROM production_tasks WHERE id=?", (task_id,)).fetchone() is None:
            return LegacyTaskRemoval(task_id, 0, 0, 0, backup)
        _require_unreferenced(db, task_id)
    backup.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(root / "discovery.sqlite3")) as origin:
        with closing(sqlite3.connect(backup)) as destination:
            origin.backup(destination)
    with transaction(root) as db:
        db.execute("BEGIN IMMEDIATE")
        _require_unreferenced(db, task_id)
        observations = db.execute(
            "DELETE FROM workflow_observations WHERE subject_ref=?", (task_id,)
        ).rowcount
        events = db.execute(
            "DELETE FROM production_operator_events WHERE id=? OR actor_id=?",
            (task_id, f"editorial:{task_id}"),
        ).rowcount
        tasks = db.execute("DELETE FROM production_tasks WHERE id=?", (task_id,)).rowcount
    return LegacyTaskRemoval(task_id, tasks, observations, events, backup)
