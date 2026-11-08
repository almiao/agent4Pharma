"""制作调用的供应商用量快照；未知值不记零，不把缓存命中重复计为输入。"""

from collections.abc import Iterable

from pydantic import BaseModel, Field

from agent4pharma.runtime.workflow_observation import WorkflowObservation


class ProductionUsage(BaseModel):
    """一次尝试中已收到的计费回报，超时可保留部分数据。"""

    attempt: int
    requests: int = 0
    uncached_input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    total_tokens: int | None = None
    last_input_tokens: int | None = None
    compactions: int = 0
    search_calls: int = 0
    complete: bool = False
    auxiliary_usage_reported: bool = False
    updated_at: str = ""
    note: str = "仅计已收到的供应商用量；搜索服务未回报的额外用量不在合计中。"


class ProductionUsageView(BaseModel):
    """按尝试分别显示，避免把失败重试开销或未上报用量隐去。"""

    attempts: list[ProductionUsage] = Field(default_factory=list)
    reported_total_tokens: int | None = None


def summarize_usage(events: Iterable[WorkflowObservation]) -> ProductionUsageView:
    """选择每次尝试的最新累计快照；历史没有回报时返回未知。"""
    attempts: dict[int, ProductionUsage] = {}
    for event in events:
        if event.operation == "token_usage":
            value = ProductionUsage.model_validate_json(event.output_json)
            attempts[value.attempt] = value
    rows = list(attempts.values())
    totals = [row.total_tokens for row in rows]
    return ProductionUsageView(
        attempts=rows,
        reported_total_tokens=sum(totals) if totals and all(v is not None for v in totals) else None,
    )
