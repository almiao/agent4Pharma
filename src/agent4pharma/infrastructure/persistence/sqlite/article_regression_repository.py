"""历史单篇回归只读仓储及通用程序观测仓储。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction
from agent4pharma.modules.operations.article_regression_contracts import (
    ArticleRegressionRun,
)
from agent4pharma.runtime.workflow_observation import ObservationPage, WorkflowObservation


@dataclass
class RegressionRepository:
    """只读旧单篇运行及冻结引用，不创建或推进执行。"""

    root: Path

    def get(self, identity: str) -> ArticleRegressionRun:
        """读取实际回归账本。"""
        with transaction(self.root) as connection:
            row = connection.execute(
                "SELECT payload FROM article_regressions WHERE id=?", (identity,)
            ).fetchone()
        if row is None:
            raise KeyError("回归运行不存在")
        return ArticleRegressionRun.model_validate_json(row[0])

    def list(self) -> list[ArticleRegressionRun]:
        """最多 100 条回归历史，独立于正式每日批次。"""
        with transaction(self.root) as connection:
            return [
                ArticleRegressionRun.model_validate_json(row[0])
                for row in connection.execute(
                    "SELECT payload FROM article_regressions ORDER BY rowid DESC LIMIT 100"
                )
            ]


@dataclass
class ObservationRepository:
    """保存业务步骤与模型工具实际输入输出，不保存隐藏思维链。"""

    root: Path

    def append(self, event: WorkflowObservation) -> None:
        """观测提交独立短连接，不持有模型或网络调用的事务。"""
        with transaction(self.root) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO workflow_observations(id,subject_ref,payload) VALUES (?,?,?)",
                (event.id, event.subject_ref, event.model_dump_json()),
            )

    def page(
        self, subjects: list[str], after: int = 0, limit: int = 50, *, operations: tuple[str, ...] = ()
    ) -> ObservationPage:
        """按数据库 seq 翻页，多读一条判断后续页；只读取明确主体。"""
        if after < 0 or not 1 <= limit <= 100:
            raise ValueError("观测分页参数无效")
        if not subjects:
            return ObservationPage(next_cursor=after)
        placeholders = ",".join("?" for _ in subjects)
        operation_filter = (
            " AND json_extract(payload,'$.operation') IN (" + ",".join("?" for _ in operations) + ")"
            if operations
            else ""
        )
        with transaction(self.root) as connection:
            rows = connection.execute(
                f"SELECT seq,payload FROM workflow_observations WHERE subject_ref IN ({placeholders}) "
                "AND seq>?" + operation_filter + " ORDER BY seq LIMIT ?",
                [*subjects, after, *operations, limit + 1],
            ).fetchall()
        selected = rows[:limit]
        return ObservationPage(
            items=[WorkflowObservation.model_validate_json(row[1]) for row in selected],
            next_cursor=selected[-1][0] if selected else after,
            has_more=len(rows) > limit,
        )

    def list(
        self, subjects: list[str], after: int = 0, *, operations: tuple[str, ...] = ()
    ) -> list[WorkflowObservation]:
        """限定主体范围最多 1000 条观测，类型筛选在 LIMIT 前执行，不解码无关正文。"""
        if not subjects:
            return []
        placeholders = ",".join("?" for _ in subjects)
        operation_filter = (
            " AND json_extract(payload,'$.operation') IN (" + ",".join("?" for _ in operations) + ")"
            if operations
            else ""
        )
        with transaction(self.root) as connection:
            return [
                WorkflowObservation.model_validate_json(row[0])
                for row in connection.execute(
                    f"SELECT payload FROM workflow_observations WHERE subject_ref IN ({placeholders}) "
                    "AND seq>?" + operation_filter + " ORDER BY seq LIMIT 1000",
                    [*subjects, after, *operations],
                )
            ]
