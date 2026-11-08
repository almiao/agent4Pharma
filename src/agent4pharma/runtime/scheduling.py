"""计算北京时间 08:00 到期的业务日期及中断后需要补跑的日期，不启动后台进程。"""

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from agent4pharma.runtime.execution_interfaces import ScheduleStore
from agent4pharma.runtime.settings import ApplicationSettings


def due_days(
    config: ApplicationSettings,
    store: ScheduleStore,
    now: datetime | None = None,
    *,
    collect_only: bool = False,
    workflow_completed: Callable[[str], bool] | None = None,
) -> list[date]:
    """First startup processes yesterday; later startups catch up missed daily dates."""
    now = (now or datetime.now(ZoneInfo(config.timezone))).astimezone(ZoneInfo(config.timezone))
    last_due = now.date() - timedelta(days=1 if now.time() >= time(8) else 2)
    rows = store.daily_jobs()
    matching = []

    for row in rows:
        request = row["request"]
        if request["policy"] == config.snapshot():
            status = row["status"]
            if not collect_only and workflow_completed and workflow_completed(row["id"]):
                report = store.report(row["id"])
                if report["coverage_status"] == "complete" and not report.get("awaiting_triage"):
                    status = "complete"
            if collect_only and status != "complete":
                report = store.report(row["id"])
                status = report["coverage_status"]
                if report.get("awaiting_triage"):
                    status = "partial"
            matching.append((date.fromisoformat(request["day"]), status))
    if not matching:
        return [last_due]
    days = {d for d, status in matching if status != "complete" and d <= last_due}
    latest = max(d for d, _ in matching)
    current = latest + timedelta(days=1)
    while current <= last_due:
        days.add(current)
        current += timedelta(days=1)
    return sorted(days)
