"""程序步骤与模型工具的可审计输入输出，不收集隐藏推理或凭证。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from agent4pharma.runtime.execution_trace import ExecutionIdentity


class WorkflowObservation(BaseModel):
    """一次实际动作的观测；输入输出为应用校验后的业务数据。"""

    id: str
    subject_ref: str
    stage: str
    operation: str
    status: Literal["started", "succeeded", "failed", "blocked", "skipped"]
    input_json: str = Field(default="", max_length=250000)
    output_json: str = Field(default="", max_length=250000)
    detail: str = Field(default="", max_length=4000)
    created_at: str
    execution: ExecutionIdentity | None = Field(default=None, exclude_if=lambda value: value is None)


class ObservationPage(BaseModel):
    """插入序游标由持久化实现生成，不以事件数量猜测游标。"""

    items: list[WorkflowObservation] = Field(default_factory=list)
    next_cursor: int = 0
    has_more: bool = False


class ObservationSink(Protocol):
    """观测持久化端口；不执行任何业务状态迁移。"""

    def append(self, event: WorkflowObservation) -> None:
        """追加实际步骤，幂等保留事件身份。"""
        ...

    def list(
        self, subjects: list[str], after: int = 0, *, operations: tuple[str, ...] = ()
    ) -> list[WorkflowObservation]:
        """按插入序读取指定主体，可在读取正文前筛选操作类型；禁止跨任务读取。"""
        ...

    def page(
        self, subjects: list[str], after: int = 0, limit: int = 50, *, operations: tuple[str, ...] = ()
    ) -> ObservationPage:
        """增量读取明确任务范围和稳定持久化游标。"""
        ...


def read_observations(
    sink: ObservationSink, subjects: list[str], operations: tuple[str, ...]
) -> Iterator[WorkflowObservation]:
    """按持久化游标读取指定类型全部观测，聚合不能把列表上限当作完成。"""
    cursor = 0
    while True:
        page = sink.page(subjects, cursor, 100, operations=operations)
        yield from page.items
        if not page.has_more:
            return
        if page.next_cursor <= cursor:
            raise ValueError("观测分页游标未推进")
        cursor = page.next_cursor


def bounded_observation_json(serialized: str) -> str:
    """大载荷保存明确截断标记与内容哈希；保持有效 JSON，完整产物仍由业务仓储拥有。"""
    if len(serialized) <= 245000:
        return serialized
    return json.dumps(
        {
            "truncated": True,
            "original_characters": len(serialized),
            "sha256": hashlib.sha256(serialized.encode()).hexdigest(),
            "preview": serialized[:30000],
        },
        ensure_ascii=False,
    )
