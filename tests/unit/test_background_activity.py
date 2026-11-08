from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.workflows.background_activity import (
    BackgroundActivity,
    activity_snapshot,
    paper_continuation_activity,
    production_activity,
)


def item(identity, status, updated, **kwargs):
    return BackgroundActivity(
        id=identity,
        kind="test",
        title="Task",
        status=status,
        current_step="Step",
        updated_at=updated.isoformat(),
        href="/console",
        **kwargs,
    )


def test_stale_running_and_expired_lease_are_not_counted_as_live():
    now = datetime(2026, 11, 1, tzinfo=UTC)
    live = item("live", "running", now)
    stale = item("stale", "running", now - timedelta(minutes=16))
    expired = item("expired", "running", now, expires_at=(now - timedelta(seconds=1)).isoformat())
    snapshot = activity_snapshot([live, stale, expired], now)
    assert snapshot.counts["running"] == 1
    assert snapshot.counts["unknown"] == 2
    assert stale.status == "running"
    assert "lease_token" not in snapshot.model_dump_json()


def test_queued_work_is_not_truncated_by_recent_history():
    now = datetime(2026, 11, 1, tzinfo=UTC)
    old = now - timedelta(days=2)
    snapshot = activity_snapshot(
        [item("queue", "queued", old), item("done", "complete", old), item("new", "complete", now)], now
    )
    assert {i.id for i in snapshot.items} == {"queue", "new"}
    assert snapshot.counts["queued"] == 1
    assert snapshot.counts["complete"] == 1


def test_expired_pdf_confirmation_is_attention_not_waiting():
    now = datetime(2026, 11, 1, tzinfo=UTC)
    job = item("pdf", "waiting", now, expires_at=(now - timedelta(seconds=1)).isoformat())
    job.kind = "PDF 下载"
    snapshot = activity_snapshot([job], now)
    assert snapshot.items[0].status == "attention"
    assert snapshot.counts["waiting"] == 0


def test_production_task_detail_opens_exact_task_in_workspace():
    task = ProductionTask.model_construct(
        id="task-write",
        story_id="story-human",
        role="writer",
        phase="draft-writing",
        input_json="{}",
        status="queued",
        attempt=0,
        updated_at="2026-11-02T00:00:00+00:00",
    )
    activity = production_activity(task)
    query = parse_qs(urlsplit(activity.href).query)
    assert activity.title == "初稿撰写任务"
    assert query == {
        "view": ["workspace"],
        "stage": ["production"],
        "story": ["story-human"],
        "task": ["task-write"],
    }


def test_waiting_continuation_does_not_duplicate_its_queued_production_task():
    paper = SimpleNamespace(continuation_status="waiting", continuation_task_id="task-next")
    run = SimpleNamespace(items=[paper])
    assert paper_continuation_activity(run, {"task-next"}) == []


def test_resumed_pause_is_history_instead_of_a_second_current_activity():
    task = ProductionTask.model_construct(
        id="paused-stage",
        role="writer",
        phase="draft-writing",
        input_json="{}",
        status="paused",
        attempt=1,
        updated_at="2026-11-02T00:00:00+00:00",
    )
    assert production_activity(task).status == "paused"
    resumed = production_activity(task, has_successor=True)
    assert resumed.status == "attention" and "后继任务" in resumed.current_step
