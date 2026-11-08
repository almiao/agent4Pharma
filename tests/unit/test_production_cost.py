"""当前价估算的缓存分类、时段边界、幂等累计和未知用量回归。"""

import pytest

from agent4pharma.runtime.production_cost import estimate_production_cost, price_multiplier
from agent4pharma.runtime.production_usage import ProductionUsage
from agent4pharma.runtime.workflow_observation import WorkflowObservation


def usage(attempt=1, n=1, timestamp="2026-11-05T12:00:00+00:00", **changes):
    values = dict(
        attempt=attempt,
        requests=n,
        uncached_input_tokens=1000000 * n,
        output_tokens=1000000 * n,
        cache_read_tokens=1000000 * n,
        complete=True,
    )
    values.update(changes)
    return WorkflowObservation(
        id=f"{attempt}-{n}",
        subject_ref="task",
        stage="content",
        operation="token_usage",
        status="succeeded",
        created_at=timestamp,
        output_json=ProductionUsage(**values).model_dump_json(),
    )


def cost(events, model="deepseek-flash", endpoint="https://api.deepseek.com"):
    return estimate_production_cost(events, model, endpoint)


def test_prices_cumulative_deltas_and_failed_retries_without_double_counting():
    events = [usage(), usage(n=2), usage(n=2), usage(attempt=2)]
    assert cost(events).amount == "15.060000"
    assert not cost(events).partial
    assert cost([usage()], model="deepseek-v4-pro").amount == "18.150000"
    assert cost([usage()], model="deepseek-v4-flash").amount == "5.020000"


@pytest.mark.parametrize(
    ("time", "expected"),
    [
        ("2026-11-05T08:59:59+08:00", 1),
        ("2026-11-05T09:00:00+08:00", 2),
        ("2026-11-05T12:00:00+08:00", 1),
        ("2026-11-05T14:00:00+08:00", 2),
        ("2026-11-05T18:00:00+08:00", 1),
        ("2026-11-07T10:00:00+08:00", 1),
        ("2026-10-01T10:00:00+08:00", 1),
        ("2026-02-28T10:00:00+08:00", 1),
        ("2027-11-05T10:00:00+08:00", None),
        ("2026-11-05T10:00:00", None),
    ],
)
def test_beijing_price_boundaries_and_unknown_calendar(time, expected):
    assert price_multiplier(time) == expected


def test_cross_period_usage_is_split_by_report_time():
    assert cost([usage(timestamp="2026-11-05T11:59:00+08:00"), usage(n=2)]).amount == "15.060000"


def test_unknown_usage_model_endpoint_and_decreasing_totals():
    assert cost([]).amount is None
    assert cost([usage(cache_read_tokens=None)]).amount is None
    assert cost([usage()], model="unknown").amount is None
    assert cost([usage()], endpoint="https://proxy.example").amount is None
    assert cost([usage(n=2), usage()]).amount is None
    assert cost([usage(complete=False)]).partial
    mixed = cost([usage(), usage(attempt=2, cache_read_tokens=None)])
    assert mixed.partial and mixed.amount == "5.020000"


def test_lifecycle_cost_includes_failed_attempts_and_exact_descendants():
    from agent4pharma.runtime.production_execution import ProductionLimits, ProductionTask
    from agent4pharma.runtime.production_lifecycle_cost import summarize_lifecycle_cost

    root = ProductionTask(
        id="root",
        actor_id="writer:root",
        role="writer",
        phase="draft-writing",
        model="deepseek-flash",
        endpoint="https://api.deepseek.com",
        prompt="p",
        prompt_hash="h",
        input_json="{}",
        limits=ProductionLimits(),
        created_at="now",
        updated_at="now",
        status="succeeded",
    )
    child = root.model_copy(
        update={"id": "retry", "parent_task_id": "root", "status": "failed", "error": "cap"}
    )
    other = root.model_copy(update={"id": "same-title-but-unrelated"})
    review = root.model_copy(update={"id": "review", "parent_task_id": "retry", "status": "queued"})
    first = usage(total_tokens=3000000)
    retry = usage(attempt=2, total_tokens=3000000)
    result = summarize_lifecycle_cost(
        [root, child, other, review], {"root": [first, first], "retry": [first, retry]}, "root"
    )
    assert result.requests == 3 and result.reported_total_tokens == 9000000
    assert result.estimated_cny == "15.060000"
    assert result.pending_task_ids == ["review"] and result.failed_task_ids == ["retry"]
    assert result.partial
    assert len(result.tasks) == 3 and result.tasks[1].failure_fingerprint
