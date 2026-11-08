"""按已上报用量估算制作节点费用；当前价重估历史，不冒充供应商账单。"""

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlsplit

from pydantic import BaseModel

from agent4pharma.runtime.production_usage import ProductionUsage
from agent4pharma.runtime.workflow_observation import WorkflowObservation

PRICE_SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
PRICE_VERSION = "deepseek-2026-11-05"
# 国办发明电〔2025〕7号；周末即使调休上班也按供应商周末空闲规则处理。
HOLIDAYS_2026 = ((1, 1, 3), (2, 15, 23), (4, 4, 6), (5, 1, 5), (6, 19, 21), (9, 25, 27), (10, 1, 7))
RATES = {
    "deepseek-flash": ("1", "0.02", "4"),
    "deepseek-v4-flash": ("1", "0.02", "4"),
    "deepseek-v4-flash-vision-exp": ("1", "0.02", "4"),
    "deepseek-v4-pro": ("4.5", "0.15", "13.5"),
}


class ProductionCost(BaseModel):
    """人民币已知用量小计，未知或不完整状态与金额同时返回。"""

    currency: str = "CNY"
    amount: str | None = None
    partial: bool = True
    pricing_version: str = PRICE_VERSION
    source: str = PRICE_SOURCE
    note: str = "按当前价及用量回报时间估算；含失败尝试，不含未上报用量与搜索/抓取费，非账单。"


def price_multiplier(timestamp: str) -> int | None:
    """以北京时间回报时刻判时段；未配置年份的工作日高峰保持未知。"""
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    local = moment.astimezone(timezone(timedelta(hours=8)))
    peak_hours = 9 <= local.hour < 12 or 14 <= local.hour < 18
    if local.weekday() >= 5 or not peak_hours:
        return 1
    if local.year != 2026:
        return None
    holiday = any(local.month == month and start <= local.day <= end for month, start, end in HOLIDAYS_2026)
    return 1 if holiday else 2


def estimate_production_cost(
    events: Iterable[WorkflowObservation], model: str, endpoint: str
) -> ProductionCost:
    """按尝试对累计快照取差，不重复计结尾回报；仅估官方端点的已知模型。"""
    result = ProductionCost()
    if urlsplit(endpoint).hostname != "api.deepseek.com" or model not in RATES:
        result.note = "模型或服务端点无已核实定价，费用未知。"
        return result
    rates = tuple(Decimal(rate) for rate in RATES[model])
    previous: dict[int, ProductionUsage] = {}
    amount, counted, unknown = Decimal(0), False, False
    fields = ("uncached_input_tokens", "cache_read_tokens", "output_tokens")
    for event in events:
        if event.operation != "token_usage":
            continue
        row = ProductionUsage.model_validate_json(event.output_json)
        old = previous.get(row.attempt)
        previous[row.attempt] = row
        if not row.requests:
            continue
        values = [getattr(row, field) for field in fields]
        prior = [getattr(old, field) for field in fields] if old else [0, 0, 0]
        if any(value is None for value in [*values, *prior]) or row.cache_write_tokens not in (None, 0):
            unknown = True
            continue
        deltas = [value - prior_value for value, prior_value in zip(values, prior, strict=True)]
        if any(delta < 0 for delta in deltas):
            # 累计值回退不能静默变成负费用或随后重复计费。
            result.note = "用量累计值发生回退，费用未知；请核对调用记录。"
            return result
        if not any(deltas):
            counted = counted or old is None
            continue
        multiplier = price_multiplier(event.created_at)
        if multiplier is None:
            unknown = True
            continue
        amount += sum(Decimal(n) * rate for n, rate in zip(deltas, rates, strict=True)) * multiplier / 1000000
        counted = True
    result.amount = format(amount.quantize(Decimal("0.000001")), "f") if counted else None
    result.partial = unknown or not counted or any(not row.complete for row in previous.values())
    return result
