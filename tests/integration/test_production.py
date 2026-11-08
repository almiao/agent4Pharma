"""真实 SQLite/用例/MCP 的离线制作测试；不以替身提交冒充真实模型质量。"""

import asyncio
import json
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import partial

import httpx
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from agent4pharma.composition import build_maintenance_workbench, open_application_storage
from agent4pharma.entrypoints.web.production_api import ProductionAPI
from agent4pharma.entrypoints.web.server import WorkbenchHTTPServer
from agent4pharma.infrastructure.persistence.sqlite.production_unit import production_unit
from agent4pharma.modules.content.draft_contracts import (
    ClaimEvidence,
    DraftSubmission,
    ResearchEvidence,
    validate_draft,
)
from agent4pharma.modules.editorial.commission_contracts import (
    CommissionBrief,
    EditorialDecision,
)
from agent4pharma.modules.publishing.release_contracts import ChannelSubmission
from agent4pharma.modules.review.review_contracts import ReviewIssue, ReviewSubmission, validate_review
from agent4pharma.runtime.production_execution import ProductionLimits
from agent4pharma.runtime.settings import ApplicationSettings
from agent4pharma.workflows.newsroom_skills import load_newsroom_skills
from agent4pharma.workflows.production_agent_tools import EvidenceLookup, ProductionAgentTools
from agent4pharma.workflows.production_dependencies import CandidateSnapshot, ProductionContext
from agent4pharma.workflows.production_queue import ProductionRoleDefinition, PublishedRoleDefinitions
from agent4pharma.workflows.production_tasks import ControlProductionTask
from agent4pharma.workflows.production_workbench import ProductionWorkbench


def submit_reading_ready(tools):
    from agent4pharma.modules.review.reader_review_contracts import ReaderObservation, ReaderReviewSubmission

    result = ReaderObservation(
        news_understood="The experiment reports a bounded result.",
        reason_to_read="Understand the measured change.",
        first_obstacle="No material obstacle.",
    )
    if "get_reader_review_context" in tools.app.execution.get(tools.task_id).context_tools:
        tools.get_reader_review_context()
        tools.record_reader_observation(result)
    else:
        tools.get_reader_draft()
        tools.save_reader_observation(result)
        tools.get_reader_brief()
    tools.submit_reader_review(
        ReaderReviewSubmission(
            decision="ready",
            summary="Both reading dimensions are complete.",
            appeal_and_flow="completed",
            language_naturalness="completed",
        )
    )


def test_new_task_budget_preserves_legacy_snapshot_defaults():
    legacy = ProductionLimits.model_validate({"max_tokens": 4096, "timeout_seconds": 180})
    assert legacy.reasoning_effort == "high"
    assert legacy.max_revision_rounds is None
    assert "max_revision_rounds" not in legacy.model_dump()
    current = ProductionLimits.for_new_task()
    assert (current.max_tokens, current.timeout_seconds, current.reasoning_effort) == (16384, 1000, "low")
    assert current.max_revision_rounds == 5
    assert ProductionLimits.model_validate_json(current.model_dump_json()) == current


class DeferredExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, *args):
        self.calls.append(args)

    def shutdown(self, **kwargs):
        pass


def deferred_production_api(app):
    api = ProductionAPI(app, lambda _: pytest.fail("deferred execution must not run inline"), lambda: True)
    api.executor.shutdown(wait=True)
    api.executor = DeferredExecutor()
    return api


def control_task(app, task, action):
    return app.execution.control(
        ControlProductionTask(
            task_id=task.id,
            action=action,
            expected_status=task.status,
            expected_attempt=task.attempt,
        )
    )


def test_pause_fences_late_worker_and_resume_keeps_frozen_stage(production):
    app, _ = production
    queued = app.start(["a"], ProductionLimits())
    running = app.execution.claim(queued.id)
    paused = control_task(app, running, "pause")
    assert paused.status == "paused" and not paused.lease_token
    assert not app.execution.is_current(running)
    with pytest.raises(ValueError, match="租约"):
        app.context(running.id, running.lease_token)
    app.execution.fail(running, "late worker exception")
    assert app.execution.get(running.id).status == "paused"
    with pytest.raises(ValueError, match="不可领取"):
        app.execution.claim(running.id)
    resumed = control_task(app, paused, "resume")
    assert resumed.status == "queued" and resumed.attempt == 0
    assert resumed.parent_task_id == paused.id
    assert resumed.prompt == running.prompt and resumed.limits == running.limits
    assert resumed.model == running.model and resumed.prompt_hash == running.prompt_hash
    assert json.loads(resumed.input_json)["candidates"] == json.loads(running.input_json)["candidates"]
    assert control_task(app, paused, "resume").id == resumed.id
    with pytest.raises(ValueError, match="后继"):
        control_task(app, paused, "stop")
    assert app.execution.get(paused.id).attempt == 1


def test_task_controls_reject_stale_state_and_preserve_failed_attempts(production):
    app, _ = production
    queued = app.start(["a"], ProductionLimits())
    running = app.execution.claim(queued.id)
    with pytest.raises(ValueError, match="状态已变化"):
        control_task(app, queued, "pause")
    app.execution.fail(running, "saved failure")
    failed = app.execution.get(running.id)
    retry = control_task(app, failed, "retry")
    assert retry.id != failed.id and retry.parent_task_id == failed.id
    assert retry.status == "queued" and not retry.error
    assert app.execution.get(failed.id).error == "saved failure"
    assert control_task(app, failed, "retry").id == retry.id
    with pytest.raises(ValueError, match="后继"):
        app.execution.claim(failed.id)


def test_stopped_task_can_queue_explicit_retry_but_not_stale_story(production):
    app, _ = production
    story, _ = commission(app)
    task = pending(app, "writer")
    stopped = control_task(app, task, "stop")
    assert stopped.status == "cancelled"
    with pytest.raises(ValueError, match="不可领取"):
        app.execution.claim(stopped.id)
    # 旧库可能已有取消的选题；删除管理入口不能让旧阶段绕过状态校验。
    with app.unit() as work:
        saved = work.stories.get(story)
        work.stories.save(
            saved.model_copy(update={"status": "cancelled", "revision": saved.revision + 1}), saved.revision
        )
    with pytest.raises(ValueError, match="任务书"):
        control_task(app, stopped, "retry")


def test_task_control_resume_automatically_schedules_without_extra_authorization(production):
    app, _ = production
    task = app.start(["a"], ProductionLimits())
    api = deferred_production_api(app)
    try:
        pause = api.post(
            "/api/production/task-control",
            {
                "task_id": task.id,
                "action": "pause",
                "expected_status": "queued",
                "expected_attempt": 0,
            },
        )
        assert pause["status"] == "paused" and "lease_token" not in pause
        resumed = api.post(
            "/api/production/task-control",
            {
                "task_id": task.id,
                "action": "resume",
                "expected_status": "paused",
                "expected_attempt": 0,
            },
        )
        assert resumed["status"] == "queued" and resumed["auto_run"]
        assert resumed["id"] in api.pending and len(api.executor.calls) == 1
        api.model_available = lambda: False
        with pytest.raises(ValueError, match="凭证"):
            api.post("/api/production/run", {"task_id": resumed["id"]})
    finally:
        api.close()


def test_paper_flow_keeps_uncommissioned_stages_and_savepoint_identities(production):
    app, _ = production
    root = app.start(["a"], ProductionLimits()).model_copy(
        update={
            "phase": "topic-selection",
            "status": "succeeded",
            "result_ref": "context-task",
        }
    )
    context = root.model_copy(
        update={
            "id": "context-task",
            "parent_task_id": root.id,
            "phase": "draft-writing",
            "result_ref": "pitch-task",
        }
    )
    pitch = root.model_copy(
        update={
            "id": "pitch-task",
            "parent_task_id": context.id,
            "phase": "fact-check",
            "status": "blocked",
            "result_ref": "",
            "error": "old capacity restriction",
        }
    )
    with app.unit() as work:
        for task in (root, context, pitch):
            work.tasks.save(task)
    retry = control_task(app, pitch, "retry")
    flow = app.snapshot().paper_flows[0]
    assert flow.paper_titles == ["Protein design"] and flow.candidate_ids == ["a"]
    assert set(flow.task_ids) == {root.id, context.id, pitch.id, retry.id}
    assert set(flow.savepoint_task_ids) == {root.id, context.id}
    assert flow.leaf_task_ids == [retry.id] and flow.story_ids == []
    assert "Observed activity" not in flow.model_dump_json()


@pytest.fixture
def production(tmp_path):
    open_application_storage(tmp_path).close()
    evidence = ResearchEvidence(
        id="paper-v1",
        title="Protein design",
        text="Observed activity was 10%.",
        origin="operator_document",
        fulltext=True,
    )
    candidates = [
        CandidateSnapshot(
            id=name,
            assessment_id="assessment-" + name,
            summary="Protein research",
            reason="New controlled result",
            evidence=[evidence],
        )
        for name in ("a", "b")
    ]
    definitions = {
        role: ProductionRoleDefinition(
            role=role, prompt="Test fixed role", model="offline", endpoint="https://example.test"
        )
        for role in ("editor", "writer", "reviewer", "publisher")
    }
    app = ProductionWorkbench(
        partial(production_unit, tmp_path),
        lambda ids: [c for c in candidates if c.id in ids],
        definitions,
        lambda query: [evidence],
        lambda url: evidence,
    )
    return app, tmp_path


def brief(**kwargs):
    return CommissionBrief(**({"title": "研究解读", "angle": "解释证据边界"} | kwargs))


def commission(app, candidate="a", *, limits=None, **kwargs):
    queued = app.start([candidate], limits or ProductionLimits())
    task = app.execution.claim(queued.id)
    story = app.handoff.editorial(
        task.id,
        task.lease_token,
        EditorialDecision(
            candidate_ids=[candidate],
            decision="commission",
            reason="有明确新结果值得研究者理解证据边界。",
            brief=brief(**kwargs),
        ),
    )
    return story, queued


def pending(app, role):
    return next(task for task in app.snapshot().tasks if task.role == role and task.status == "queued")


def draft(**kwargs):
    return DraftSubmission(
        title="证据解读",
        body="Observed activity was 10%. "
        + "这是研究中的实验结果，需要结合基线、测量条件和实际样本范围理解。" * 4,
        summary="解释条件与结果。",
        claims=[
            ClaimEvidence(
                claim="Observed activity was 10%.", evidence_id="paper-v1", quote="Observed activity was 10%."
            )
        ],
        coverage="已回答读者问题并说明这不是临床结果。",
        **kwargs,
    )


def submit_content(app, **kwargs):
    task = app.execution.claim(pending(app, "writer").id)
    identity = app.handoff.content(task.id, task.lease_token, draft(**kwargs))
    return identity, task


def review(app, result=None):
    task = app.execution.claim(pending(app, "reviewer").id)
    app.handoff.review(
        task.id,
        task.lease_token,
        result or ReviewSubmission(decision="pass", summary="关键表述符合证据，任务问题已经清楚回答。"),
    )
    return task


def package(app):
    task = app.execution.claim(pending(app, "publisher").id)
    return app.handoff.publishing(
        task.id,
        task.lease_token,
        ChannelSubmission(channel="local_html", release_note="从准确审核版本生成本地 HTML，不对外发布。"),
    )


def approve_current(app, package_id):
    from agent4pharma.modules.publishing.human_approval import ApprovePackage, package_fingerprint
    from agent4pharma.workflows.human_package_approval import approve_package

    with app.unit() as work:
        item = next(p for p in work.releases.packages() if p.id == package_id)
        return approve_package(
            work,
            ApprovePackage(
                package_id=item.id,
                expected_fingerprint=package_fingerprint(item),
                reason="人工核对正文与最终排版后确认定稿。",
            ),
        )


def test_human_approval_required_idempotent_and_bound_to_whole_package(production):
    from agent4pharma.modules.publishing.human_approval import ApprovePackage
    from agent4pharma.workflows.human_package_approval import approve_package
    from agent4pharma.workflows.human_review_views import human_review_index

    app, root = production
    commission(app)
    submit_content(app)
    review(app)
    identity = package(app)
    with pytest.raises(ValueError, match="批准"):
        app.export_package(identity)
    with app.unit() as work:
        with pytest.raises(ValueError, match="已变化"):
            approve_package(
                work,
                ApprovePackage(package_id=identity, expected_fingerprint="0" * 64, reason="人工确认本版本"),
            )
        assert work.releases.approvals() == []
    approval = approve_current(app, identity)
    assert approve_current(app, identity) == approval
    app.export_package(identity)
    with app.unit() as work:
        index = human_review_index(work)
        assert index.items[0].approval == approval
        assert index.items[0].exported
        assert index.items[0].package is None
        assert len(work.releases.approvals()) == 1
    assert list((root / "backups").glob("discovery-pre-v11-*.sqlite3"))


def test_relayout_needs_new_approval_and_pending_work_blocks_existing_approval(production):
    from agent4pharma.modules.publishing.article_layout import LayoutRequest

    app, _ = production
    commission(app)
    submit_content(app)
    review(app)
    original = package(app)
    approve_current(app, original)
    changed = app.create_layout(LayoutRequest(package_id=original))
    with pytest.raises(ValueError, match="批准"):
        app.export_package(changed.id)
    approve_current(app, changed.id)
    task = app.queue_layout(changed.id, ProductionLimits())
    with pytest.raises(ValueError, match="任务"):
        app.export_package(changed.id)
    app.execution.fail(app.execution.claim(task.id), "offline check")


def test_real_workspace_http_read_and_approval_contract(production):
    from agent4pharma.infrastructure.persistence.sqlite.article_regression_repository import (
        ObservationRepository,
    )
    from agent4pharma.runtime.workflow_observation import WorkflowObservation

    app, root = production
    story, queued = commission(app)
    submit_content(app)
    review(app)
    identity = package(app)
    sink = ObservationRepository(root)
    sink.append(
        WorkflowObservation(
            id="event-1",
            subject_ref=queued.id,
            stage="editor",
            operation="tool",
            status="succeeded",
            input_json='{"title":"read"}',
            created_at="2026-11-02T00:00:00+00:00",
        )
    )
    api = ProductionAPI(app, lambda _: pytest.fail("must not execute"), lambda: False, sink)
    try:
        index = api.get("/api/production/human-review?story=" + story)
        assert index["items"][0]["package"]["id"] == identity
        assert api.get("/api/production/human-review?story=other")["items"] == []
        assert api.get(f"/api/production/tasks/{queued.id}/observations")["items"][0]["id"] == "event-1"
        item = index["items"][0]
        api.post(
            "/api/production/approve-package",
            {
                "package_id": item["id"],
                "expected_fingerprint": item["fingerprint"],
                "reason": "人工确认完整发布版本",
            },
        )
        assert api.post("/api/production/export", {"package_id": identity})["extension"] == "html"
    finally:
        api.close()


def test_four_roles_atomic_handoff_and_local_export(production):
    app, root = production
    story, queued = commission(app)
    assert app.start(["a"], ProductionLimits()).id == queued.id
    submit_content(app)
    review(app)
    identity = package(app)
    approve_current(app, identity)
    body, extension = app.export_package(identity)
    assert extension == "html" and "Observed activity" in body
    app.export_package(identity)
    with app.unit() as work:
        assert work.stories.get(story).status == "completed"
        assert len(work.drafts.list()) == len(work.reviews.list()) == len(work.releases.packages()) == 1
        assert [r.status for r in work.releases.receipts()] == ["exported"]
        assert all(t.status == "succeeded" for t in work.tasks.list())
    with sqlite3.connect(root / "discovery.sqlite3") as connection:
        assert connection.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 13
        assert connection.execute("SELECT count(*) FROM production_operator_events").fetchone()[0] == 4


def test_rejected_submission_rolls_back_output_and_handoff(production):
    app, _ = production
    commission(app)
    task = app.execution.claim(pending(app, "writer").id)
    wrong = draft().model_copy(
        update={
            "claims": [
                ClaimEvidence(
                    claim="Observed activity was 10%.",
                    evidence_id="other-story",
                    quote="Observed activity was 10%.",
                )
            ]
        }
    )
    with pytest.raises(ValueError, match="引用"):
        app.handoff.content(task.id, task.lease_token, wrong)
    with app.unit() as work:
        assert work.drafts.list() == []
        assert work.tasks.get(task.id).status == "running"
        assert not any(t.role == "reviewer" for t in work.tasks.list())
    app.handoff.content(task.id, task.lease_token, draft())


def test_revision_loop_and_independent_adjudication(production):
    app, _ = production
    story, _ = commission(app, limits=ProductionLimits(max_revision_rounds=1))
    submit_content(app)
    required = ReviewSubmission(
        decision="changes_requested",
        summary="需要明确基线条件，当前缺少关键限制。",
        issues=[
            ReviewIssue(
                id="baseline",
                severity="required",
                location="正文第 1 段",
                reason="基线条件不够明确，影响读者理解实验收益。",
                resolution="补充基线的适用条件并避免扩大推断。",
            )
        ],
    )
    review(app, required)
    with pytest.raises(ValueError, match="逐项"):
        submit_content(app)
    running = next(t for t in app.snapshot().tasks if t.role == "writer" and t.status == "running")
    app.handoff.content(running.id, running.lease_token, draft(resolved_issue_ids=["baseline"]))
    review(app, required)
    with app.unit() as work:
        assert work.stories.get(story).status == "deferred"
        assert not any(t.role == "publisher" for t in work.tasks.list())
    resumed = app.resume(story, "reviewer", ProductionLimits())
    assert resumed.role == "reviewer"
    review(app)
    package(app)


def test_stopped_stage_rejects_late_submission_and_historical_cancel_stays_closed(production):
    app, _ = production
    story, _ = commission(app)
    task = app.execution.claim(pending(app, "writer").id)
    control_task(app, task, "stop")
    with app.unit() as work:
        saved = work.stories.get(story)
        work.stories.save(
            saved.model_copy(update={"status": "cancelled", "revision": saved.revision + 1}), saved.revision
        )
    with pytest.raises(ValueError, match="租约"):
        app.handoff.content(task.id, task.lease_token, draft())
    with pytest.raises(ValueError, match="取消"):
        app.resume(story, "reviewer", ProductionLimits())


def test_historical_taskbook_revision_invalidates_old_package(production):
    app, _ = production
    story, _ = commission(app)
    submit_content(app)
    review(app)
    identity = package(app)
    with app.unit() as work:
        saved = work.stories.get(story)
        work.stories.save(
            saved.model_copy(
                update={"revision": saved.revision + 1, "brief_version": saved.brief_version + 1}
            ),
            saved.revision,
        )
    with pytest.raises(ValueError, match="失效"):
        app.export_package(identity)
    snapshot = app.artifact_summaries()
    preview = next(item for item in snapshot.items if item.id == identity)
    assert preview.validity == "invalid" and not preview.current
    assert not any(item.current for item in snapshot.items if item.kind == "review")


@pytest.mark.parametrize(
    "route", ["/api/production/supply", "/api/production/resume-research", "/api/production/manage"]
)
def test_retired_manual_management_routes_cannot_change_production(production, route):
    app, _ = production
    commission(app)
    before = app.snapshot().model_dump(exclude={"as_of"})
    api = ProductionAPI(app, app.execution.get, lambda: False)
    try:
        with pytest.raises(KeyError, match="制作接口不存在"):
            api.post(route, {})
        assert app.snapshot().model_dump(exclude={"as_of"}) == before
    finally:
        api.close()


def test_current_artifact_projection_retires_review_when_rework_creates_new_draft(production):
    app, _ = production
    commission(app)
    first_id, _ = submit_content(app)
    review(
        app,
        ReviewSubmission(
            decision="changes_requested",
            summary="需要说明基线条件，避免读者误读实验结果。",
            issues=[
                ReviewIssue(
                    id="baseline",
                    severity="required",
                    location="结果段",
                    reason="缺少清楚的对照条件，结论范围尚不明确。",
                    resolution="补充对照条件并解释该实验结果的适用范围。",
                )
            ],
        ),
    )
    before = app.artifact_summaries()
    assert len([a for a in before.items if a.kind == "review" and a.current]) == 1
    assert len([link for link in before.task_links if link.current and link.rework]) == 1
    second_id, _ = submit_content(app, resolved_issue_ids=["baseline"])
    after = app.artifact_summaries()
    assert {a.id for a in after.items if a.kind == "draft" and a.current} == {second_id}
    assert not next(a for a in after.items if a.id == first_id).current
    assert not any(a.current for a in after.items if a.kind == "review")


def test_concurrent_commissions_queue_without_business_capacity_limit(production):
    app, _ = production
    tasks = [app.execution.claim(app.start([candidate], ProductionLimits()).id) for candidate in ("a", "b")]

    def submit(pair):
        candidate, task = pair
        try:
            return app.handoff.editorial(
                task.id,
                task.lease_token,
                EditorialDecision(
                    candidate_ids=[candidate],
                    decision="commission",
                    reason="明确新研究值得制作并解释证据的实际边界。",
                    brief=brief(),
                ),
            )
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, zip(("a", "b"), tasks)))
    assert sum(value is not None for value in results) == 2
    assert len(app.snapshot().stories) == 2
    with app.unit() as work:
        assert len([t for t in work.tasks.list() if t.role == "writer"]) == 2


def test_expired_lease_fences_old_model_and_budget(production):
    app, _ = production
    task = app.execution.claim(app.start(["a"], ProductionLimits()).id)
    with app.unit() as work:
        work.tasks.save(
            task.model_copy(
                update={"lease_until": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}
            )
        )
    replacement = app.execution.claim(task.id)
    with pytest.raises(ValueError, match="租约"):
        app.context(task.id, task.lease_token)
    app.execution.fail(replacement, "测试失败")
    with pytest.raises(ValueError, match="额度"):
        app.execution.claim(task.id)


def test_model_failure_and_role_tools_cannot_forge_success(production):
    app, _ = production
    queued = app.start(["a"], ProductionLimits())

    class Broken:
        def run(self, task):
            raise RuntimeError("secret provider diagnostic")

    result = app.execution.execute(queued.id, Broken())
    assert result.status == "failed" and "secret" not in result.error
    task = app.execution.claim(queued.id)
    tools = ProductionAgentTools(app, task.id, task.lease_token)
    with pytest.raises(ValueError, match="角色"):
        tools.submit_review(
            ReviewSubmission(decision="pass", summary="这不是授权的审核角色，因此不能提交审核。")
        )
    with pytest.raises(ValueError, match="自"):
        validate_review(
            ReviewSubmission(decision="pass", summary="同一作者不能自行批准稿件通过核验。"),
            "author",
            "author",
        )


def test_production_http_contract_requires_explicit_execution(production):
    app, _ = production
    api = ProductionAPI(app, lambda identity: pytest.fail("不应执行模型"), lambda: False)
    try:
        with pytest.raises(ValueError, match="凭证"):
            api.post("/api/production/start", {"candidate_ids": ["a"]})
        assert not app.snapshot().tasks
        assert len(api.get("/api/production")["stages"]) == 4
        assert api.get("/api/production/artifacts")["packages"] == []
        assert api.get("/api/production/artifact-summaries")["items"] == []
    finally:
        api.close()


def test_flow_artifact_summaries_preserve_versions_without_text_or_side_effects(production):
    app, _ = production
    commission(app)
    draft_id, _ = submit_content(app)
    review(app)
    package_id = package(app)
    before = app.snapshot().model_dump(exclude={"as_of"})
    index = app.artifact_summaries()
    assert {item.kind for item in index.items} == {"draft", "review", "package"}
    decision = index.decisions[0]
    assert decision.created_at == app.execution.get(decision.task_id).updated_at
    report = next(item for item in index.items if item.kind == "review")
    preview = next(item for item in index.items if item.kind == "package")
    assert report.reference_id == draft_id and report.status == "pass"
    assert preview.id == package_id and preview.reference_id == report.id
    assert preview.status == "local_preview"
    assert "Observed activity" not in index.model_dump_json()
    assert "body" not in index.model_dump_json() and "quote" not in index.model_dump_json()
    assert app.snapshot().model_dump(exclude={"as_of"}) == before
    with app.unit() as work:
        assert work.releases.receipts() == []


@pytest.mark.parametrize("with_skills", [False, True])
@pytest.mark.parametrize("role", ["editor", "writer", "reviewer", "publisher"])
def test_real_production_mcp_role_allowlist(production, role, with_skills):
    app, root = production
    if with_skills and role != "publisher":
        app.definitions[role] = app.definitions[role].model_copy(
            update={"newsroom_skills": load_newsroom_skills(role)}
        )
    if role == "editor":
        queued = app.start(["a"], ProductionLimits())
    else:
        commission(app)
        if role in {"reviewer", "publisher"}:
            submit_content(app)
        if role == "publisher":
            review(app)
        queued = pending(app, role)
    task = app.execution.claim(queued.id)

    async def check():
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "agent4pharma",
                "production-mcp",
                "--root",
                str(root),
                "--task",
                task.id,
                "--lease",
                task.lease_token,
            ],
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                submission = {
                    "editor": "submit_editorial_decision",
                    "writer": "submit_draft",
                    "reviewer": "submit_review",
                    "publisher": "submit_channel_package",
                }[role]
                expected = {"get_production_context", "report_production_blocked", submission}
                if task.newsroom_skills is not None:
                    expected |= {"list_newsroom_skills", "read_newsroom_resource"}
                if role == "publisher":
                    expected |= {"find_paper_figures", "inspect_paper_figures"}
                if role == "writer":
                    expected |= {"search_local_materials", "read_bound_source"}
                if role in {"writer", "reviewer"}:
                    expected |= {"find_bound_evidence", "read_document"}
                    if task.newsroom_skills and task.newsroom_skills.context_policy in {
                        "research-checkpoint-v2",
                        "stage-working-set-v1",
                    }:
                        expected.add("find_bound_evidence_batch")
                if role in {"editor", "writer", "reviewer"}:
                    expected.add("read_evidence")
                assert {t.name for t in (await session.list_tools()).tools} == expected
                if task.newsroom_skills is not None:
                    listing = await session.call_tool("list_newsroom_skills", {})
                    assert not listing.is_error
                    body = await session.call_tool("read_newsroom_resource", {"resource": "workflow.md"})
                    assert not body.is_error
                    assert (
                        json.loads(body.content[0].text)["sha256"]
                        == task.newsroom_skills.read("workflow.md").sha256
                    )
                    rejected = await session.call_tool("read_newsroom_resource", {"resource": "../secret"})
                    assert rejected.is_error
                response = await session.call_tool("get_production_context", {})
                assert not response.is_error
                data = response.structured_content or json.loads(response.content[0].text)
                if role == "editor":
                    assert data["candidates"][0]["id"] == "a"
                else:
                    assert data["story"]["id"] == task.story_id
                if role == "writer":
                    invalid = draft().model_copy(
                        update={
                            "claims": [
                                ClaimEvidence(
                                    claim="Not in body",
                                    evidence_id="unknown",
                                    quote="Not in evidence",
                                )
                            ]
                        }
                    )
                    rejected = await session.call_tool("submit_draft", {"result": invalid.model_dump()})
                    assert rejected.is_error
                    assert "claims[0]" in rejected.content[0].text
                    assert app.execution.get(task.id).status == "running"

    asyncio.run(check())


def test_skill_snapshot_survives_definition_change_and_checks_lease(production):
    app, _ = production
    app.definitions["editor"] = app.definitions["editor"].model_copy(
        update={"newsroom_skills": load_newsroom_skills("editor")}
    )
    queued = app.start(["a"], ProductionLimits())
    assert app.start(["a"], ProductionLimits()).id == queued.id
    original = queued.newsroom_skills.content_hash
    app.definitions["editor"] = app.definitions["editor"].model_copy(
        update={"newsroom_skills": load_newsroom_skills("newsroom")}
    )
    assert app.start(["a"], ProductionLimits()).id != queued.id
    task = app.execution.claim(queued.id)
    tools = ProductionAgentTools(app, task.id, task.lease_token)
    assert tools.list_newsroom_skills().content_hash == original
    with pytest.raises(ValueError, match="不属于"):
        tools.read_newsroom_resource("skills/draft-writing/SKILL.md")
    with pytest.raises(ValueError, match="租约"):
        ProductionAgentTools(app, task.id, "wrong").list_newsroom_skills()
    assert app.execution.get(task.id).reads == 0
    app.execution.fail(task, "offline retry")
    retried = app.execution.claim(task.id)
    assert retried.newsroom_skills.content_hash == original
    with pytest.raises(ValueError, match="租约"):
        tools.read_newsroom_resource("workflow.md")


def test_legacy_task_does_not_silently_receive_current_skills(production):
    app, _ = production
    task = app.execution.claim(app.start(["a"], ProductionLimits()).id)
    assert "newsroom_skills" not in task.model_dump()
    with pytest.raises(ValueError, match="历史任务"):
        ProductionAgentTools(app, task.id, task.lease_token).list_newsroom_skills()


def test_fulltext_is_required_and_url_is_not_fulltext():
    evidence = ResearchEvidence(
        id="paper-v1",
        title="Metadata",
        text="Observed activity was 10%.",
        url="https://example.test/paper",
        origin="primary_page",
        fulltext=False,
    )
    with pytest.raises(ValueError, match="全文"):
        validate_draft(draft(), [evidence], True)
    validate_draft(draft(), [evidence], False)


def test_research_scope_and_persistent_read_budget(production):
    app, _ = production
    commission(app)
    task = pending(app, "writer")
    with app.unit() as work:
        work.tasks.save(task.model_copy(update={"limits": ProductionLimits(max_reads=1)}))
    task = app.execution.claim(task.id)
    with pytest.raises(ValueError, match="登记"):
        app.research(task.id, task.lease_token, "https://private.example.test/", url=True)
    assert app.research(task.id, task.lease_token, "protein")
    with pytest.raises(ValueError, match="额度"):
        app.research(task.id, task.lease_token, "protein")


def test_handoff_transaction_rolls_back_when_next_stage_cannot_queue(production):
    app, _ = production
    task = app.execution.claim(app.start(["a"], ProductionLimits()).id)
    definition = app.definitions.pop("writer")
    with pytest.raises(KeyError):
        app.handoff.editorial(
            task.id,
            task.lease_token,
            EditorialDecision(
                candidate_ids=["a"],
                decision="commission",
                reason="实际新信息值得制作并说明研究结果的边界。",
                brief=brief(),
            ),
        )
    assert app.snapshot().stories == []
    assert app.execution.get(task.id).status == "running"
    app.definitions["writer"] = definition


def test_v7_backup_restores_v6_without_rewriting_candidates(tmp_path):
    from agent4pharma.infrastructure.persistence.sqlite.migrations.v001 import SCHEMA
    from agent4pharma.infrastructure.persistence.sqlite.migrations.v002 import MIGRATION_2
    from agent4pharma.infrastructure.persistence.sqlite.migrations.v003 import MIGRATION_3
    from agent4pharma.infrastructure.persistence.sqlite.migrations.v004 import MIGRATION_4
    from agent4pharma.infrastructure.persistence.sqlite.migrations.v005 import MIGRATION_5
    from agent4pharma.infrastructure.persistence.sqlite.migrations.v006 import MIGRATION_6

    with sqlite3.connect(tmp_path / "discovery.sqlite3") as connection:
        connection.executescript(SCHEMA)
        connection.execute("INSERT INTO schema_version(version) VALUES (1)")
        connection.commit()
        for script in [MIGRATION_2, MIGRATION_3, MIGRATION_4, MIGRATION_5, MIGRATION_6]:
            connection.executescript(script)
        connection.execute(
            "INSERT INTO materials(id,identity,title,identifiers,first_seen_at) "
            "VALUES ('old','doi:old','Preserved','{}','2026-10-29')"
        )
    open_application_storage(tmp_path).close()
    backup = next((tmp_path / "backups").glob("discovery-pre-v7-*.sqlite3"))
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 6
        assert connection.execute("SELECT title FROM materials WHERE id='old'").fetchone()[0] == "Preserved"
        assert not connection.execute(
            "SELECT name FROM sqlite_master WHERE name='production_tasks'"
        ).fetchone()
    with sqlite3.connect(tmp_path / "discovery.sqlite3") as connection:
        assert connection.execute("SELECT title FROM materials WHERE id='old'").fetchone()[0] == "Preserved"


def test_http_page_and_csrf_protected_production_routes(production):
    app, root = production
    maintenance = build_maintenance_workbench(
        root / "config.toml", ApplicationSettings(data_dir=root, sources={"biorxiv": {"query": ""}})
    )
    api = deferred_production_api(app)
    server = WorkbenchHTTPServer(maintenance, 0, api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
            response = client.get("/console?stage=editorial&scope=all")
            assert response.status_code == 200 and server.token in response.text
            assert "script-src 'self'" in response.headers["content-security-policy"]
            assert client.get("/assets/stage_page.js").status_code == 404
            assert client.get("/assets/console.css").status_code == 404
            assert client.get("/api/production").status_code == 403
            assert client.get("/api/production/artifact-summaries").status_code == 403
            headers = {"X-Workbench-Token": server.token}
            snapshot = client.get("/api/production", headers=headers)
            assert snapshot.status_code == 200 and len(snapshot.json()["stages"]) == 4
            assert snapshot.json()["external_delivery"] is False
            summary = client.get("/api/production/artifact-summaries", headers=headers)
            assert summary.status_code == 200 and summary.json() == {
                "items": [],
                "task_links": [],
                "decisions": [],
                "receipts": [],
                "intermediates": [],
            }
            queued = client.post("/api/production/start", headers=headers, json={"candidate_ids": ["a"]})
            assert queued.status_code == 200 and queued.json()["status"] == "queued"
            assert "lease_token" not in queued.json()
            denied = client.post(
                "/api/production/start",
                headers={**headers, "Origin": "https://evil.test"},
                json={"candidate_ids": ["b"]},
            )
            assert denied.status_code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        api.close()
        maintenance.close()


def test_bound_evidence_excerpt_offsets_and_scope(production):
    app, _ = production
    story, _ = commission(app)
    task = app.execution.claim(pending(app, "writer").id)
    result = app.find_evidence(task.id, task.lease_token, "paper-v1", "activity")
    assert len(result) == 1 and result[0].text == "Observed activity was 10%."
    assert result[0].start == 0 and result[0].end == len(result[0].text)
    assert len(result[0].content_hash) == 64
    assert app.execution.get(task.id).reads == 1
    with pytest.raises(ValueError, match="证据"):
        app.find_evidence(task.id, task.lease_token, "other-story", "activity")
    assert app.find_evidence(task.id, task.lease_token, "paper-v1", "missing") == []


def test_bound_evidence_search_tolerates_pdf_whitespace_without_changing_quotes(production):
    app, _ = production
    story, _ = commission(app)
    text = "prefix " + "encoder on\n both  expression\tand motif" + " suffix"
    with app.unit() as work:
        work.drafts.add_evidence(
            story,
            ResearchEvidence(
                id="pdf-layout", title="PDF", text=text, origin="operator_document", fulltext=True
            ),
        )
    task = app.execution.claim(pending(app, "writer").id)
    excerpts = app.find_evidence(
        task.id, task.lease_token, "pdf-layout", "encoder on both expression and motif"
    )
    assert len(excerpts) == 1
    excerpt = excerpts[0]
    assert excerpt.text == text[excerpt.start : excerpt.end] == text
    assert "on\n both  expression\tand" in excerpt.text
    assert app.find_evidence(task.id, task.lease_token, "pdf-layout", "expression.*motif") == []


def test_document_catalog_is_lazy_and_read_spans_are_saved_exactly(production):
    from agent4pharma.modules.content.draft_contracts import EvidenceSpan
    from agent4pharma.modules.materials.document_reading import (
        DocumentContent,
        DocumentReadResult,
        DocumentReference,
    )

    app, _ = production
    reference = DocumentReference(id="download", sha256="a" * 64, pages=23)
    metadata = ResearchEvidence(
        id="document", title="PDF", text="仅文档目录", origin="primary_document", document=reference
    )
    candidates = app.candidates(["a"])
    app.candidates = lambda ids: [
        c.model_copy(update={"evidence": [metadata]}) for c in candidates if c.id in ids
    ]
    story_id, queued = commission(app)
    original_snapshot = app.execution.get(queued.id).input_json
    task = app.execution.claim(pending(app, "writer").id)
    calls = []

    def read_document(ref, start, end, after):
        calls.append((ref, start, end, after))
        return DocumentReadResult(
            status="available",
            content=DocumentContent(
                sha256=ref.sha256,
                short_id="aaaaaaa",
                tier="standard",
                content="Observed\nactivity was 10%.\n" + "Supplementary context. " * 25,
            ),
        )

    app.document_reader = read_document
    tools = ProductionAgentTools(app, task.id, task.lease_token)
    context = tools.get_production_context()
    assert not calls and "Observed activity" not in context.model_dump_json()
    with pytest.raises(ValueError, match="1至3页"):
        tools.read_document("document", 1, 23)
    result = tools.read_document("document", 1, 1)
    assert len(calls) == 1
    excerpt = tools.find_bound_evidence(result.evidence_id, "activity")[0]
    assert not tools.find_bound_evidence(result.evidence_id, "OBSERVED ACTIVITY")
    assert tools.find_bound_evidence(result.evidence_id, "OBSERVED ACTIVITY", ignore_case=True) == [excerpt]
    short = tools.find_bound_evidence(result.evidence_id, "activity", compact=True)[0]
    assert short.start == excerpt.start and short.end < excerpt.end
    assert short.content_hash == excerpt.content_hash
    assert short.text == result.text[short.start : short.end]
    assert tools.find_bound_evidence_batch(
        [
            EvidenceLookup(evidence_id=result.evidence_id, query="activity"),
            EvidenceLookup(evidence_id=result.evidence_id, query="not on this page"),
        ]
    ) == [[short], []]
    quote = "Observed\nactivity was 10%."
    proposal = draft().model_copy(
        update={
            "claims": [
                ClaimEvidence(
                    claim="Observed activity was 10%.",
                    evidence_id=result.evidence_id,
                    source_span=EvidenceSpan(
                        content_hash=short.content_hash, start=short.start, end=len(quote)
                    ),
                )
            ]
        }
    )
    draft_id = tools.submit_draft(proposal)
    with app.unit() as work:
        saved = work.drafts.get(draft_id)
        assert saved.submission.claims[0].quote == quote
        assert not any(e.id == "paper-v1" for e in work.drafts.evidence(story_id))
    assert app.execution.get(queued.id).input_json == original_snapshot


def test_evidence_read_cannot_dump_whole_source_or_other_story(production):
    app, _ = production
    commission(app)
    task = app.execution.claim(pending(app, "writer").id)
    tools = ProductionAgentTools(app, task.id, task.lease_token)
    excerpt = tools.read_evidence("paper-v1", 2, 4)
    assert excerpt.text == "serv" and excerpt.start == 2 and excerpt.end == 6
    bounded = tools.read_evidence("paper-v1", 0, 60000)
    assert len(bounded.text) <= 6000 and bounded.end == len(bounded.text)
    with pytest.raises(ValueError, match="6000"):
        app.read_evidence(task.id, task.lease_token, "paper-v1", 0, 60000)
    with pytest.raises(ValueError, match="不属于"):
        tools.read_evidence("other-story")


def test_batch_lookup_budget_rejects_before_consuming_partial_results(production):
    app, _ = production
    commission(app)
    task = app.execution.claim(pending(app, "writer").id)
    with app.unit() as work:
        work.tasks.save(
            task.model_copy(
                update={
                    "newsroom_policy": "news-focus-v2",
                    "citation_lookups": tuple(str(i) for i in range(31)),
                }
            )
        )
    tools = ProductionAgentTools(app, task.id, task.lease_token)
    queries = [EvidenceLookup(evidence_id="paper-v1", query=q) for q in ("Observed", "activity")]
    with pytest.raises(ValueError, match="剩余1.*本批未执行"):
        tools.find_bound_evidence_batch(queries)
    assert len(app.execution.get(task.id).citation_lookups) == 31
    assert tools.find_bound_evidence_batch(queries[:1])[0]
    assert len(app.execution.get(task.id).citation_lookups) == 32
    assert tools.find_bound_evidence_batch(queries[:1])[0]


def test_production_summary_does_not_load_published_execution_resources(production):
    app, _ = production
    _, task = commission(app)

    def unavailable_prompt(role: str) -> str:
        raise FileNotFoundError(role)

    app.definitions = PublishedRoleDefinitions(app.definitions, unavailable_prompt)
    api = ProductionAPI(app, lambda _: pytest.fail("read only"), lambda: False)
    try:
        summary = api.get("/api/production?view=summary")
        assert task.id in {item["id"] for item in summary["tasks"]}
        assert {item["id"]: item["model"] for item in summary["stages"]} == {
            role: definition.model for role, definition in app.definitions.defaults.items()
        }
        with pytest.raises(FileNotFoundError):
            app.definitions["writer"]
    finally:
        api.close()


def test_cost_summary_and_detail_include_more_than_observation_list_limit(production):
    from agent4pharma.infrastructure.persistence.sqlite.article_regression_repository import (
        ObservationRepository,
    )
    from agent4pharma.runtime.production_usage import ProductionUsage
    from agent4pharma.runtime.workflow_observation import WorkflowObservation

    app, root = production
    _, task = commission(app)
    with app.unit() as work:
        work.tasks.save(
            task.model_copy(update={"model": "deepseek-flash", "endpoint": "https://api.deepseek.com"})
        )
    sink = ObservationRepository(root)
    for n in range(1, 1003):
        row = ProductionUsage(
            attempt=1,
            requests=n,
            uncached_input_tokens=n * 1000,
            cache_read_tokens=0,
            output_tokens=0,
            complete=n == 1002,
        )
        sink.append(
            WorkflowObservation(
                id=f"usage-{n}",
                subject_ref=task.id,
                stage=task.role,
                operation="token_usage",
                status="succeeded",
                created_at="2026-11-05T12:00:00+00:00",
                output_json=row.model_dump_json(),
            )
        )
    api = ProductionAPI(app, lambda _: pytest.fail("read only"), lambda: False, sink)
    try:
        summary = next(t for t in api.get("/api/production?view=summary")["tasks"] if t["id"] == task.id)
        detail = api.get("/api/production/tasks/" + task.id)["task"]
        assert summary["cost"] == detail["cost"]
        assert detail["cost"]["amount"] == "1.002000"
        assert detail["cost"]["partial"] is False
    finally:
        api.close()


def enable_newsroom_pipeline(app):
    for role, definition in list(app.definitions.items()):
        app.definitions[role] = definition.model_copy(
            update={
                "newsroom_pipeline": True,
                "newsroom_skills": load_newsroom_skills(role) if role != "publisher" else None,
            }
        )


def read_stage_skills(app, task):
    tools = ProductionAgentTools(app, task.id, task.lease_token)
    for resource in task.required_resources:
        tools.read_newsroom_resource(resource)
    return tools


def background_packet(*, planned=False):
    from agent4pharma.modules.content.background_research import BackgroundResearch

    finding = writing_packet().findings[0].model_copy(update={"kind": "background"})
    return BackgroundResearch(
        focus="Explain what the reported assay can support for this selected news angle.",
        questions=[
            {
                "id": "assay-context",
                "question": "What context is needed to interpret the assay?",
                "purpose": "explanation",
                "relevance": "The selected news question depends on the meaning of the reported assay.",
                "status": "planned" if planned else "answered",
                "answer": "" if planned else "The bound paper supplies a specific measured activity.",
                "findings": [] if planned else [finding],
                "handling": "qualify" if planned else "use",
                "assessment": "Read the bound material first."
                if planned
                else "Enough for this assay explanation.",
            }
        ],
        sufficiency="in_progress" if planned else "sufficient",
        assessment="Need to examine the source."
        if planned
        else "Enough to explain this specific news result.",
        research_scope="The bound source only; no external search was needed.",
    )


def test_background_plan_results_and_handoff_are_real_savepoints(production):
    from agent4pharma.modules.content.background_research import BackgroundResearch

    app, _ = production
    enable_newsroom_pipeline(app)
    task = app.execution.claim(app.start(["a"], ProductionLimits()).id)
    tools = read_stage_skills(app, task)
    plan = tools.save_background_research(background_packet(planned=True))
    assert plan.version == 1 and plan.phase == "topic-selection" and not plan.story_id
    assert tools.save_background_research(background_packet(planned=True)) == plan
    assert tools.save_background_research(background_packet(planned=True), receipt_only=True) == plan.version
    with pytest.raises(ValueError, match="版本"):
        tools.save_background_research(background_packet())
    result = tools.save_background_research(background_packet(), expected_version=1)
    saved = app.execution.get(task.id)
    assert saved.status == "running" and saved.lease_token == task.lease_token
    assert saved.input_json == task.input_json and not saved.result_ref
    assert len(app.snapshot().tasks) == 1
    assert app.context(tools.task_id, tools.token).background_research == [result]
    api = deferred_production_api(app)
    try:
        detail = api.get("/api/production/tasks/" + task.id)
        assert detail["background_research"][0]["version"] == 2
        assert [v["version"] for v in detail["background_research_versions"]] == [1, 2]
        assert detail["background_research_excerpts"][0]["text"] == "Observed activity was 10%."
        assert detail["background_research_sources"][0]["title"] == "Protein design"
        assert "background_research_json" not in detail["task"]
    finally:
        api.close()
    tools.submit_editorial_decision(
        EditorialDecision(
            candidate_ids=["a"], decision="commission", reason="A bounded report.", brief=brief()
        )
    )
    writer = app.execution.claim(pending(app, "writer").id)
    writing = read_stage_skills(app, writer)
    assert app.context(writing.task_id, writing.token).background_research == [result]
    assert writing.get_task_checkpoint().background_write_expected_version == 0
    assert writing.get_task_checkpoint().writing.write_expected_version == 0
    assert writing.get_task_checkpoint().missing_required_resources == []
    reused = writing.save_background_research(
        BackgroundResearch(
            focus="Use the editor's existing background for this article without expanding its angle.",
            sufficiency="sufficient",
            assessment="The editor already answered the sole necessary explanatory background question.",
            research_scope="Reused the editor's bound source; no new questions or searches were necessary.",
        )
    )
    assert reused.story_id == writer.story_id and reused.brief_version == 1
    assert writing.get_task_checkpoint().background_write_expected_version == 1
    writing.save_writing_research(writing_packet())
    writing.submit_draft(draft())
    review = app.execution.claim(pending(app, "reviewer").id)
    assert app.context(review.id, review.lease_token).background_research == [result, reused]
    with pytest.raises(ValueError, match="未开放"):
        read_stage_skills(app, review).save_background_research(background_packet())


def test_background_savepoint_fences_leases_sources_and_stale_briefs(production):
    app, _ = production
    writer, tools = commission_writer(app)
    packet = background_packet()
    foreign = packet.questions[0].findings[0].model_copy(update={"evidence_id": "other-paper"})
    with pytest.raises(ValueError, match="未绑定"):
        tools.save_background_research(
            packet.model_copy(
                update={"questions": [packet.questions[0].model_copy(update={"findings": [foreign]})]}
            )
        )
    with pytest.raises(ValueError, match="租约"):
        ProductionAgentTools(app, writer.id, "wrong").save_background_research(packet)
    first = tools.save_background_research(packet)
    paused = control_task(app, app.execution.get(writer.id), "pause")
    with pytest.raises(ValueError, match="租约"):
        tools.save_background_research(packet)
    resumed = app.execution.claim(control_task(app, paused, "resume").id)
    resumed_tools = read_stage_skills(app, resumed)
    assert app.context(resumed_tools.task_id, resumed_tools.token).background_research == [first]
    assert not resumed.background_research_json
    assert app.execution.get(writer.id).background_research_json
    changed = packet.model_copy(
        update={"assessment": "Rechecked the specific angle; existing background remains sufficient."}
    )
    second = resumed_tools.save_background_research(changed, expected_version=0)
    assert second.version == 1 and second.task_id == resumed.id
    with app.unit() as work:
        story = work.stories.get(resumed.story_id)
        work.stories.save(story.model_copy(update={"revision": story.revision + 1}), story.revision)
    with pytest.raises(ValueError, match="任务书"):
        resumed_tools.save_background_research(packet, expected_version=2)


def test_background_contract_distinguishes_unknown_failure_and_sufficient():
    from pydantic import ValidationError

    from agent4pharma.modules.content.background_research import BackgroundResearch

    value = background_packet().model_dump()
    value["questions"][0]["findings"] = []
    with pytest.raises(ValidationError, match="答案和证据"):
        BackgroundResearch.model_validate(value)
    value = background_packet(planned=True).model_dump()
    value["sufficiency"] = "sufficient"
    with pytest.raises(ValidationError, match="调研中"):
        BackgroundResearch.model_validate(value)
    value["questions"][0].update(status="technical_failure", handling="block")
    with pytest.raises(ValidationError, match="核心背景阻断"):
        BackgroundResearch.model_validate(value)
    value["sufficiency"] = "insufficient"
    assert BackgroundResearch.model_validate(value).questions[0].status == "technical_failure"


def writing_packet():
    import hashlib

    from agent4pharma.modules.content.draft_contracts import EvidenceSpan
    from agent4pharma.modules.content.research_contracts import ResearchFinding
    from agent4pharma.modules.content.writing_research import WritingResearch

    text = "Observed activity was 10%."
    packet = WritingResearch(
        question="What did the activity assay establish?",
        answer="The study reports measured activity under specific experimental conditions.",
        findings=[
            ResearchFinding(
                kind="finding",
                statement="Observed activity was 10% in the reported assay.",
                evidence_id="paper-v1",
                source_span=EvidenceSpan(
                    content_hash=hashlib.sha256(text.encode()).hexdigest(), start=0, end=len(text)
                ),
            )
        ],
        research_scope="Only the bound paper was read; no external background was needed.",
    )

    from agent4pharma.modules.content.news_outline import NewsOutline, OutlineParagraph, OutlineSource

    finding = packet.findings[0]
    return packet.model_copy(
        update={
            "outline": NewsOutline(
                mainline="Explain the measured activity and its experimental meaning.",
                progression="Introduce the result, then explain its experimental context.",
                paragraphs=[
                    OutlineParagraph(
                        question="What activity did the assay measure?",
                        message="The experiment observed ten percent activity.",
                        position_reason="Start with the concrete result before explaining the context.",
                        sources=[
                            OutlineSource(
                                evidence_id=finding.evidence_id,
                                source_span=finding.source_span,
                                location="已绑定文本片段",
                                use="Support the measured activity in this assay.",
                            )
                        ],
                    )
                ],
                ending="End after explaining the measured result.",
            )
        }
    )


def commission_writer(app):
    enable_newsroom_pipeline(app)
    editor = app.execution.claim(app.start(["a"], ProductionLimits()).id)
    assert editor.phase == "topic-selection" and editor.web_search_enabled
    read_stage_skills(app, editor).submit_editorial_decision(
        EditorialDecision(
            candidate_ids=["a"], decision="commission", reason="A bounded report.", brief=brief()
        )
    )
    writer = app.execution.claim(pending(app, "writer").id)
    assert writer.phase == "draft-writing" and writer.web_search_enabled
    return writer, read_stage_skills(app, writer)


def test_writer_research_checkpoint_is_versioned_and_handed_to_editor(production):
    from agent4pharma.workflows.writing_research import writing_research_versions

    app, _ = production
    writer, tools = commission_writer(app)
    frozen = writer.input_json
    first = tools.save_writing_research(writing_packet())
    assert first.version == 1 and first.research.question == writing_packet().question
    assert first.angle == brief().angle
    assert tools.save_writing_research(writing_packet()) == first
    assert tools.save_writing_research(writing_packet(), receipt_only=True) == first.version
    updated = writing_packet().model_copy(
        update={"answer": "The experiment reports ten percent activity in its specified assay."}
    )
    with pytest.raises(ValueError, match="版本"):
        tools.save_writing_research(updated)
    second = tools.save_writing_research(updated, expected_version=1)
    assert second.version == 2
    assert app.context(tools.task_id, tools.token).writing_research == second
    recovery = tools.get_task_checkpoint()
    assert recovery.writing.write_expected_version == second.version
    assert second.research.answer not in recovery.model_dump_json()
    saved = app.execution.get(writer.id)
    assert saved.status == "running" and saved.lease_token == writer.lease_token
    assert saved.input_json == frozen and not saved.result_ref
    assert len(app.snapshot().tasks) == 2
    assert writing_research_versions(saved) == [first, second]
    tools.submit_draft(draft())
    editor = app.execution.claim(pending(app, "reviewer").id)
    assert not editor.web_search_enabled
    assert app.context(editor.id, editor.lease_token).writing_research == second
    review_tools = read_stage_skills(app, editor)
    assert editor.phase == "reading-review"
    submit_reading_ready(review_tools)
    editor = app.execution.claim(pending(app, "reviewer").id)
    review_tools = read_stage_skills(app, editor)
    review_tools.submit_review(ReviewSubmission(decision="pass", summary="The reported facts are supported."))
    package_task = pending(app, "publisher")
    assert ProductionContext.model_validate_json(package_task.input_json).writing_research == second
    index = app.artifact_summaries()
    checkpoints = [row for row in index.intermediates if row.kind == "writing_research"]
    assert [(row.task_id, row.version) for row in checkpoints] == [(writer.id, 1), (writer.id, 2)]
    assert checkpoints[0].created_at == first.created_at
    assert "specified assay" not in index.model_dump_json()


@pytest.mark.parametrize("action", ["pause", "stop"])
def test_writer_saved_research_survives_controls_and_rejects_old_lease(production, action):
    app, _ = production
    writer, tools = commission_writer(app)
    snapshot = tools.save_writing_research(writing_packet())
    stopped = control_task(app, app.execution.get(writer.id), action)
    with pytest.raises(ValueError, match="租约"):
        tools.save_writing_research(writing_packet())
    queued = control_task(app, stopped, "resume" if action == "pause" else "retry")
    checkpoints = [row for row in app.artifact_summaries().intermediates if row.kind == "writing_research"]
    assert [(row.task_id, row.version) for row in checkpoints] == [(writer.id, 1)]
    assert not queued.writing_research_json
    assert app.execution.get(writer.id).writing_research_json
    current = app.execution.claim(queued.id)
    resumed = read_stage_skills(app, current)
    assert app.context(resumed.task_id, resumed.token).writing_research == snapshot
    resumed.submit_draft(draft())
    assert app.execution.get(current.id).status == "succeeded"


def test_writer_search_background_is_readable_but_cannot_replace_paper_results(production):
    from agent4pharma.modules.content.draft_contracts import EvidenceSpan
    from agent4pharma.modules.content.research_contracts import ResearchFinding

    app, _ = production
    writer, tools = commission_writer(app)
    source = ResearchEvidence(
        id="background",
        title="Prior method",
        text="Earlier methods measured related activity.",
        origin="web_search_snippet",
    )
    app.register_search_results(writer.id, writer.lease_token, [source])
    assert tools.read_evidence(source.id).text == source.text
    assert any(e.id == source.id for e in tools.list_task_sources().items)
    background = ResearchFinding(
        kind="background",
        statement=source.text,
        evidence_id=source.id,
        source_span=EvidenceSpan(content_hash=source.content_hash, start=0, end=len(source.text)),
    )
    with pytest.raises(ValueError, match="论文事实"):
        tools.save_writing_research(writing_packet().model_copy(update={"findings": [background]}))
    tools.save_writing_research(writing_packet().model_copy(update={"background": [background]}))
    tools.submit_draft(draft())
    editor = app.execution.claim(pending(app, "reviewer").id)
    assert any(e.id == source.id for e in app.context(editor.id, editor.lease_token).evidence)
    with pytest.raises(ValueError, match="未开放"):
        app.register_search_results(editor.id, editor.lease_token, [source])


def test_writer_research_blocks_only_explicit_core_gap_and_rejects_stale_story(production):
    from agent4pharma.modules.content.writing_research import WritingResearchGap

    app, _ = production
    writer, tools = commission_writer(app)
    gap = WritingResearchGap(
        question="Does the proposed angle hold?",
        handling="block",
        reason="A core factual premise remains unsupported.",
    )
    tools.save_writing_research(writing_packet().model_copy(update={"gaps": [gap]}))
    with pytest.raises(ValueError, match="核心阻断"):
        tools.submit_draft(draft())
    qualified = gap.model_copy(
        update={
            "handling": "qualify",
            "reason": "The report explicitly limits the claim to the measured assay.",
        }
    )
    tools.save_writing_research(writing_packet().model_copy(update={"gaps": [qualified]}), expected_version=1)
    with app.unit() as work:
        story = work.stories.get(writer.story_id)
        work.stories.save(story.model_copy(update={"revision": story.revision + 1}), story.revision)
    with pytest.raises(ValueError, match="任务书已变化"):
        tools.save_writing_research(writing_packet(), expected_version=2)
    with pytest.raises(ValueError, match="版本变化"):
        tools.submit_draft(draft())


def test_writer_checkpoint_remains_when_final_handoff_rolls_back(production):
    app, _ = production
    writer, tools = commission_writer(app)
    snapshot = tools.save_writing_research(writing_packet())
    app.definitions["reviewer"] = app.definitions["reviewer"].model_copy(update={"newsroom_skills": None})
    with pytest.raises(ValueError, match="冻结技能"):
        tools.submit_draft(draft())
    assert app.context(writer.id, writer.lease_token).writing_research == snapshot
    assert app.execution.get(writer.id).status == "running"
    with app.unit() as work:
        assert not work.drafts.list()


def test_research_detail_is_read_only_and_returns_exact_checkpoint_sources(production):
    app, _ = production
    writer, tools = commission_writer(app)
    snapshot = tools.save_writing_research(writing_packet())
    api = ProductionAPI(app, lambda _: pytest.fail("read only"), lambda: False)
    try:
        before = app.snapshot().tasks
        detail = api.get("/api/production/tasks/" + writer.id)
        assert detail["writing_research"]["version"] == snapshot.version
        assert detail["writing_research_excerpts"][0]["text"] == "Observed activity was 10%."
        assert detail["artifact"] is None
        assert "writing_research_json" not in detail["task"]
        assert app.snapshot().tasks == before
    finally:
        api.close()


def test_real_writer_mcp_saves_checkpoint_without_completing_task(production):
    app, root = production
    writer, _ = commission_writer(app)

    async def check():
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "agent4pharma",
                "production-mcp",
                "--root",
                str(root),
                "--task",
                writer.id,
                "--lease",
                writer.lease_token,
            ],
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert {"save_writing_research", "submit_draft", "read_document"} <= names
                assert not {"submit_reporting", "submit_editorial_decision"} & names
                response = await session.call_tool(
                    "save_writing_research", {"result": writing_packet().model_dump()}
                )
                assert not response.is_error
                assert "get_production_context" not in names
                context = await session.call_tool("get_task_checkpoint", {})
                assert not context.is_error
                returned = json.loads(context.content[0].text)
                assert returned["writing"]["write_expected_version"] == 1
                assert "context_receipt" in returned and "writing_research" not in returned
                sources = await session.call_tool("list_task_sources", {})
                assert not sources.is_error
                assert "Observed activity" not in sources.content[0].text
                denied = await session.call_tool("get_production_context", {"recovery_only": True})
                assert denied.is_error

    asyncio.run(check())
    assert app.execution.get(writer.id).status == "running"
    assert app.context(writer.id, writer.lease_token).writing_research.version == 1
    assert len(app.snapshot().tasks) == 2


def test_completed_legacy_story_is_projected_without_rewriting_archive(production):
    import json
    import sqlite3

    app, root = production
    story_id, _ = commission(app)
    with app.unit() as work:
        payload = work.stories.get(story_id).model_dump()
    payload["status"] = "completed"
    payload["brief"]["question"] = "这项变化是什么？"
    raw = json.dumps(payload, ensure_ascii=False)
    with sqlite3.connect(root / "discovery.sqlite3") as connection:
        connection.execute("UPDATE production_stories SET payload=? WHERE id=?", (raw, story_id))

    with app.unit() as work:
        story = work.stories.get(story_id)
        assert story.read_only and story.status == "completed" and story.brief.angle == brief().angle
        assert story in work.stories.list()
        with pytest.raises(ValueError, match="旧归档"):
            work.stories.save(story, story.revision)
    with sqlite3.connect(root / "discovery.sqlite3") as connection:
        assert connection.execute(
            "SELECT payload FROM production_stories WHERE id=?", (story_id,)
        ).fetchone() == (raw,)


def test_production_history_reset_backs_up_and_keeps_nonproduction_data(production):
    from agent4pharma.infrastructure.persistence.sqlite.production_history_reset import (
        reset_production_history,
    )

    app, root = production
    writer, tools = commission_writer(app)
    tools.save_writing_research(writing_packet())
    stopped = control_task(app, app.execution.get(writer.id), "stop")
    backup = root / "backups" / "before-reset.sqlite3"
    result = reset_production_history(root, backup)
    assert result.deleted["production_tasks"] == 2
    assert result.deleted["production_stories"] == 1
    assert not app.snapshot().tasks and not app.snapshot().stories
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT COUNT(*) FROM production_tasks").fetchone()[0] == 2
        assert json.loads(
            connection.execute("SELECT payload FROM production_tasks WHERE id=?", (stopped.id,)).fetchone()[0]
        )["writing_research_json"]
    assert app.candidates(["a"])[0].id == "a"
    with pytest.raises(ValueError, match="备份路径"):
        reset_production_history(root, backup)


def test_production_history_reset_rejects_active_queue_without_writing_backup(production):
    from agent4pharma.infrastructure.persistence.sqlite.production_history_reset import (
        reset_production_history,
    )

    app, root = production
    queued = app.start(["a"], ProductionLimits())
    backup = root / "backups" / "not-created.sqlite3"
    with pytest.raises(ValueError, match="运行或排队"):
        reset_production_history(root, backup)
    assert not backup.exists() and app.execution.get(queued.id) == queued


def test_newsroom_rejects_skipped_skills_wrong_stage_and_foreign_evidence(production):
    app, _ = production
    enable_newsroom_pipeline(app)
    task = app.execution.claim(app.start(["a"], ProductionLimits()).id)
    tools = ProductionAgentTools(app, task.id, task.lease_token)
    decision = EditorialDecision(
        candidate_ids=["a"], decision="commission", reason="A bounded report.", brief=brief()
    )
    with pytest.raises(ValueError, match="技能资源"):
        tools.submit_editorial_decision(decision)
    read_stage_skills(app, task).submit_editorial_decision(decision)
    writer = app.execution.claim(pending(app, "writer").id)
    tools = read_stage_skills(app, writer)
    with pytest.raises(ValueError, match="底稿"):
        tools.submit_draft(draft())
    packet = writing_packet()
    foreign = packet.findings[0].model_copy(update={"evidence_id": "foreign-evidence"})
    with pytest.raises(ValueError, match="未绑定"):
        tools.save_writing_research(packet.model_copy(update={"findings": [foreign]}))
    with pytest.raises(ValueError, match="租约"):
        ProductionAgentTools(app, writer.id, "wrong").save_writing_research(packet)
    assert len(app.snapshot().tasks) == 2 and app.execution.get(writer.id).status == "running"
    app.execution.fail(writer, "technical failure")
    retried = app.execution.claim(writer.id)
    assert retried.skill_reads == ()
    with pytest.raises(ValueError, match="租约"):
        tools.read_newsroom_resource("workflow.md")


def test_writer_cannot_skip_explanation_method_before_submitting(production):
    app, _ = production
    enable_newsroom_pipeline(app)
    pitch = app.execution.claim(app.start(["a"], ProductionLimits()).id)
    read_stage_skills(app, pitch).submit_editorial_decision(
        EditorialDecision(
            candidate_ids=["a"], decision="commission", reason="A bounded report.", brief=brief()
        )
    )
    writer = app.execution.claim(pending(app, "writer").id)
    tools = ProductionAgentTools(app, writer.id, writer.lease_token)
    explanation = "skills/draft-writing/references/reader-explanation.md"
    for resource in writer.required_resources:
        if resource != explanation:
            tools.read_newsroom_resource(resource)
    with pytest.raises(ValueError, match="技能资源"):
        tools.save_writing_research(writing_packet())
    with pytest.raises(ValueError, match="技能资源"):
        tools.submit_draft(draft())
    assert app.execution.get(writer.id).status == "running"
    tools.read_newsroom_resource(explanation)
    tools.save_writing_research(writing_packet())
    tools.submit_draft(draft())
    assert app.execution.get(writer.id).status == "succeeded"


def test_newsroom_blocked_research_retries_only_its_frozen_evidence(production):
    app, _ = production
    enable_newsroom_pipeline(app)
    task = app.execution.claim(app.start(["a"], ProductionLimits()).id)
    app.blocked(task.id, task.lease_token, "The bound paper is missing required experimental details.")
    blocked = app.execution.get(task.id)
    new = control_task(app, blocked, "retry")
    assert control_task(app, blocked, "retry").id == new.id
    assert new.phase == task.phase and new.limits == task.limits
    assert app.execution.get(task.id).input_json == task.input_json
    assert app.execution.get(task.id).status == "blocked"
    claimed = app.execution.claim(new.id)
    assert {e.id for e in app.context(claimed.id, claimed.lease_token).evidence} == {"paper-v1"}


@pytest.mark.parametrize("entry", ["import", "production_api", "retry_api"])
def test_new_pipeline_continuation_finishes_four_stages_without_manual_release(production, entry):
    from agent4pharma.workflows.continue_imported_paper import continue_imported_paper

    app, _ = production
    enable_newsroom_pipeline(app)
    calls, progress = [], []

    def execute(identity):
        task = app.execution.claim(identity)
        calls.append(task.phase)
        tools = read_stage_skills(app, task)
        if task.phase == "topic-selection":
            tools.submit_editorial_decision(
                EditorialDecision(
                    candidate_ids=["a"],
                    decision="commission",
                    reason="A bounded scientific report is justified.",
                    brief=brief(),
                )
            )
        elif task.phase == "draft-writing":
            tools.save_writing_research(writing_packet())
            assert app.execution.get(task.id).status == "running"
            tools.submit_draft(draft())
        elif task.phase == "reading-review":
            submit_reading_ready(tools)
        elif task.phase == "fact-check":
            tools.submit_review(
                ReviewSubmission(decision="pass", summary="关键表述符合证据，任务问题已经清楚回答。")
            )
        else:
            app.handoff.publishing(
                task.id,
                task.lease_token,
                ChannelSubmission(
                    channel="local_html", release_note="从准确审核版本生成本地 HTML，不对外发布。"
                ),
            )
        return app.execution.get(identity)

    original = None
    if entry == "import":
        continue_imported_paper(lambda: "a", app, execute, lambda *args: progress.append(args))
        assert progress[-1][0] == "succeeded"
    else:
        api = ProductionAPI(app, execute, lambda: True)
        try:
            if entry == "retry_api":
                original = app.execution.claim(app.start(["a"], ProductionLimits()).id)
                app.execution.fail(original, "Saved failure before retry")
                api.post(
                    "/api/production/task-control",
                    {
                        "task_id": original.id,
                        "action": "retry",
                        "expected_status": "failed",
                        "expected_attempt": 1,
                    },
                )
            else:
                api.post("/api/production/start", {"candidate_ids": ["a"]})
            api.executor.shutdown(wait=True)
            assert not api.pending and not api.execution_errors
        finally:
            api.close()
    assert calls == [
        "topic-selection",
        "draft-writing",
        "reading-review",
        "fact-check",
        "layout-production",
    ]
    with app.unit() as work:
        assert len(work.releases.packages()) == 1
    assert all(t.auto_run for t in app.snapshot().tasks if original is None or t.id != original.id)


@pytest.mark.parametrize("invalidate_lease", [False, True])
def test_precommission_pdf_read_persists_only_under_current_lease(production, invalidate_lease):
    from agent4pharma.modules.materials.document_reading import (
        DocumentContent,
        DocumentReadResult,
        DocumentReference,
    )

    app, _ = production
    enable_newsroom_pipeline(app)
    candidate = app.candidates(["a"])[0]
    metadata = ResearchEvidence(
        id="pdf",
        title="Paper",
        text="Document catalog only",
        origin="primary_document",
        document=DocumentReference(id="download", sha256="b" * 64, pages=10),
    )
    app.candidates = lambda ids: [candidate.model_copy(update={"evidence": [metadata]})]
    task = app.execution.claim(app.start(["a"], ProductionLimits()).id)

    def reader(ref, start, end, after):
        assert (start, end) == (2, 3)
        if invalidate_lease:
            app.execution.fail(task, "lease invalidated during document IO")
        return DocumentReadResult(
            status="available",
            content=DocumentContent(
                sha256=ref.sha256, short_id="bbbbbbb", tier="standard", content="Observed activity was 10%."
            ),
        )

    app.document_reader = reader
    app.cached_document_reader = reader
    tools = ProductionAgentTools(app, task.id, task.lease_token)
    if invalidate_lease:
        with pytest.raises(ValueError, match="租约"):
            tools.read_document("pdf", 2, 3)
        assert not app.execution.get(task.id).supplemental_evidence_json
    else:
        result = tools.read_document("pdf", 2, 3)
        assert result.text == "Observed activity was 10%."
        context = app.context(task.id, task.lease_token)
        assert context.story is None
        assert result.evidence_id in {e.id for e in context.evidence}
        assert app.execution.get(task.id).input_json == task.input_json
        read_stage_skills(app, task).submit_editorial_decision(
            EditorialDecision(
                candidate_ids=["a"], decision="commission", reason="A bounded report.", brief=brief()
            )
        )
        writer = pending(app, "writer")
        assert result.evidence_id in {
            e.id for e in ProductionContext.model_validate_json(writer.input_json).evidence
        }


def test_cached_pdf_repeat_does_not_read_or_return_same_text(production):
    from agent4pharma.modules.materials.document_reading import (
        DocumentContent,
        DocumentReadResult,
        DocumentReference,
    )

    app, root = production
    commission(app)
    task = app.execution.claim(pending(app, "writer").id)
    doc = DocumentReference(id="download", sha256="b" * 64, pages=10)
    source = ResearchEvidence(
        id="pdf", title="Paper", text="Catalog", origin="primary_document", document=doc
    )
    with app.unit() as work:
        work.drafts.add_evidence(task.story_id, source)
        work.tasks.save(
            task.model_copy(update={"newsroom_policy": "summary-first-v1", "phase": "draft-writing"})
        )
    calls = []

    def reader(ref, start, end, after):
        calls.append((start, end))
        return DocumentReadResult(
            status="available",
            content=DocumentContent(
                sha256=ref.sha256, short_id="bbbbbbb", tier="standard", content="Observed activity was 10%."
            ),
        )

    app.document_reader = reader
    app.cached_document_reader = reader
    first = app.read_document(task.id, task.lease_token, "pdf", 1, 1)
    second = app.read_document(task.id, task.lease_token, "pdf", 1, 1)
    assert first.text and second.text == "" and second.already_read
    assert second.evidence_id == first.evidence_id
    assert calls == [(1, 1)] and app.execution.get(task.id).reads == 1


def test_new_policy_citation_lookup_has_separate_deduplicated_budget(production):
    app, _ = production
    enable_newsroom_pipeline(app)
    task = app.execution.claim(app.start(["a"], ProductionLimits(max_reads=1)).id)
    app.read_evidence(task.id, task.lease_token, "paper-v1")
    for _ in range(3):
        found = app.find_evidence(task.id, task.lease_token, "paper-v1", "activity")
        assert found and found[0].text == "Observed activity was 10%."
    saved = app.execution.get(task.id)
    assert saved.reads == 1 and len(saved.citation_lookups) == 1
    with pytest.raises(ValueError, match="读取额度"):
        app.read_evidence(task.id, task.lease_token, "paper-v1")
    with pytest.raises(ValueError, match="证据不属于"):
        app.find_evidence(task.id, task.lease_token, "other-task-evidence", "activity")
    for index in range(31):
        app.find_evidence(task.id, task.lease_token, "paper-v1", f"missing-{index}")
    with pytest.raises(ValueError, match="32"):
        app.find_evidence(task.id, task.lease_token, "paper-v1", "another-query")
    assert app.find_evidence(task.id, task.lease_token, "paper-v1", "activity")
    app.execution.fail(task, "test terminal lease")
    with pytest.raises(ValueError, match="租约"):
        app.find_evidence(task.id, task.lease_token, "paper-v1", "activity")


def test_publication_layout_idempotence_and_export_integrity(production):
    from agent4pharma.modules.publishing.article_layout import LayoutRequest

    app, _ = production
    commission(app)
    submit_content(app)
    review(app)
    original = package(app)
    result = app.create_layout(LayoutRequest(package_id=original))
    assert result.id != original
    assert app.create_layout(LayoutRequest(package_id=original)).id == result.id
    assert result.presentation.model_calls == 0
    approve_current(app, result.id)
    assert "style=" in app.export_package(result.id, "wechat")[0]
    with app.unit() as work:
        assert len(work.releases.packages()) == 2
        changed = result.model_copy(
            update={"presentation": result.presentation.model_copy(update={"wechat_html": "tampered"})}
        )
        from agent4pharma.workflows.production_release_validation import require_current_package

        with pytest.raises(ValueError, match="公众号"):
            require_current_package(work, changed)


def test_publication_layout_rejects_unbound_figures_before_read(production):
    from agent4pharma.modules.publishing.article_layout import FigureSelection, LayoutRequest

    app, _ = production
    story, _ = commission(app)
    submit_content(app)
    review(app)
    original = package(app)
    calls = []
    app.figure_reader = lambda *args: calls.append(args)
    figure = FigureSelection(
        evidence_id="foreign", page=1, block=1, after_paragraph=1, label="原图", caption="真实图注"
    )
    with pytest.raises(ValueError, match="绑定"):
        app.create_layout(LayoutRequest(package_id=original, figures=[figure]))
    with pytest.raises(ValueError, match="段落"):
        app.create_layout(
            LayoutRequest(package_id=original, figures=[figure.model_copy(update={"after_paragraph": 999})])
        )
    assert calls == []
    with app.unit() as work:
        current = work.stories.get(story)
    with app.unit() as work:
        work.stories.save(
            current.model_copy(update={"status": "deferred", "revision": current.revision + 1}),
            current.revision,
        )
    with pytest.raises(ValueError, match="失效"):
        app.create_layout(LayoutRequest(package_id=original))


def test_publication_layout_reads_images_outside_transaction(production):
    from agent4pharma.modules.materials.document_reading import DocumentReference
    from agent4pharma.modules.publishing.article_layout import FigureImage, FigureSelection, LayoutRequest

    app, root = production
    story, _ = commission(app)
    submit_content(app)
    review(app)
    original = package(app)
    with app.unit() as work:
        evidence = work.drafts.evidence(story)[0]
        work.drafts.add_evidence(
            story,
            evidence.model_copy(
                update={
                    "id": "image-paper",
                    "document": DocumentReference(id="bound", sha256="a" * 64, pages=3),
                }
            ),
        )
    calls = []

    def reader(document, selection):
        # 写事务立即成功证明原图I/O期间没有占用原工作单元。
        with sqlite3.connect(root / "discovery.sqlite3", timeout=0.1) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        calls.append(document.sha256)
        return FigureImage(b"png-test", 200, 300, "doc:aaaaaaa/tier:standard/page:2/block:3")

    app.figure_reader = reader
    figure = FigureSelection(
        evidence_id="image-paper", page=2, block=3, after_paragraph=1, label="实验原图", caption="实验结果"
    )
    request = LayoutRequest(package_id=original, figures=[figure])
    result = app.create_layout(request)
    assert app.create_layout(request).id == result.id
    assert calls == ["a" * 64]
    assert "data:image/png;base64," in result.body
    assert result.presentation.figures[0].document_sha256 == "a" * 64
    with pytest.raises(ValueError, match="重复"):
        app.create_layout(LayoutRequest(package_id=original, figures=[figure, figure]))
    assert len(calls) == 1


def figure_task_fixture(production):
    from agent4pharma.modules.materials.document_reading import DocumentReference
    from agent4pharma.modules.publishing.article_layout import FigureImage
    from agent4pharma.modules.publishing.figure_catalog import FigureCandidate, FigureCatalog, FigurePage
    from agent4pharma.workflows.production_dependencies import ProductionContext

    app, _ = production
    commission(app)
    submit_content(app)
    review(app)
    task = app.execution.claim(pending(app, "publisher").id)
    context = ProductionContext.model_validate_json(task.input_json)
    context.evidence = [
        context.evidence[0].model_copy(
            update={"document": DocumentReference(id="pdf", sha256="a" * 64, pages=2)}
        )
    ]
    with app.unit() as work:
        work.tasks.save(task.model_copy(update={"input_json": context.model_dump_json()}))
    catalog = FigureCatalog(
        document_sha256="a" * 64,
        pages=[
            FigurePage(
                page=1,
                overview_id="p1",
                caption_context="Fig. 1 | Rescue experiment and cell dynamics.",
                candidates=[
                    FigureCandidate(figure_id=f"p1b{i}", page=1, block=i, nearby_text="Bcl6 results")
                    for i in range(1, 8)
                ],
            )
        ],
    )
    app.figure_catalog = lambda _: catalog
    calls = []

    def inspect(document, identity):
        calls.append(identity)
        return FigureImage(
            b"PNG test data", 200, 300, f"doc:aaaaaaa/tier:standard/page:1/block:{identity[3:]}"
        )

    app.figure_inspector = inspect
    app.figure_reader = lambda document, selection: inspect(document, f"p1b{selection.block}")
    return app, task, calls


def test_dsh_figure_query_and_image_budgets_are_separate_and_deduplicated(production):
    from mcp.types import ImageContent

    app, task, calls = figure_task_fixture(production)
    tools = ProductionAgentTools(app, task.id, task.lease_token)
    found = tools.find_paper_figures("paper-v1", "rescue")
    assert found.pages[0].page == 1 and found.queries_remaining == 5
    repeat = tools.find_paper_figures("paper-v1", "rescue")
    assert repeat.already_returned and repeat.pages == [] and repeat.queries_remaining == 5
    images = tools.inspect_paper_figures("paper-v1", ["p1b1", "p1b2", "p1b3"])
    assert len([x for x in images.content if isinstance(x, ImageContent)]) == 3
    repeat = tools.inspect_paper_figures("paper-v1", ["p1b1"])
    assert not any(isinstance(x, ImageContent) for x in repeat.content)
    assert calls == ["p1b1", "p1b2", "p1b3"]
    tools.inspect_paper_figures("paper-v1", ["p1b4", "p1b5", "p1b6"])
    with pytest.raises(ValueError, match="6张"):
        tools.inspect_paper_figures("paper-v1", ["p1b7"])
    for i in range(5):
        reused = tools.find_paper_figures("paper-v1", f"rescue {i}")
        assert reused.pages == [] and reused.reused_pages == [1]
    with pytest.raises(ValueError, match="查询额度"):
        tools.find_paper_figures("paper-v1", "one more")
    assert app.execution.get(task.id).reads == 0


def test_dsh_rejects_unseen_images_and_foreign_evidence(production):
    from agent4pharma.modules.publishing.article_layout import FigureSelection

    app, task, calls = figure_task_fixture(production)
    tools = ProductionAgentTools(app, task.id, task.lease_token)
    with pytest.raises(ValueError, match="绑定"):
        tools.find_paper_figures("foreign", "rescue")
    figure = FigureSelection(
        evidence_id="paper-v1",
        page=1,
        block=1,
        after_paragraph=1,
        label="图1",
        caption="结果",
        visual_observation="图中横轴包含WT与突变体分组，纵轴表示实验比例。",
    )
    result = ChannelSubmission(
        channel="local_html", release_note="按已审核正文制作带原图的图文预览。", figures=[figure]
    )
    with pytest.raises(ValueError, match="先看准确子图"):
        tools.submit_channel_package(result)
    tools.inspect_paper_figures("paper-v1", ["p1b1"])
    identity = tools.submit_channel_package(result)
    assert app.execution.get(task.id).result_ref == identity
    with app.unit() as work:
        package = next(p for p in work.releases.packages() if p.id == identity)
    assert package.presentation.selection_task_id == task.id
    assert package.presentation.total_tokens is None


def test_completed_article_can_queue_dsh_layout_without_rewriting_story(production):
    app, _ = production
    story, _ = commission(app)
    submit_content(app)
    review(app)
    source = package(app)
    with app.unit() as work:
        before = work.stories.get(story)
    queued = app.queue_layout(source, ProductionLimits())
    assert app.queue_layout(source, ProductionLimits()).id == queued.id
    task = app.execution.claim(queued.id)
    tools = ProductionAgentTools(app, task.id, task.lease_token)
    identity = tools.submit_channel_package(
        ChannelSubmission(channel="local_html", release_note="本稿未选择合适原图，保留纯文字阅读版。")
    )
    with app.unit() as work:
        assert work.stories.get(story) == before
        assert len(work.drafts.list()) == 1
        assert len(work.releases.packages()) == 2
    assert app.execution.get(task.id).status == "succeeded"
    approve_current(app, identity)
    assert app.export_package(identity)[1] == "html"


@pytest.mark.parametrize("decision", ["request_evidence", "defer"])
def test_editor_requires_distinct_revisit_conditions(production, decision):
    app, _ = production
    enable_newsroom_pipeline(app)
    task = app.execution.claim(app.start(["a"], ProductionLimits()).id)
    assert task.binary_editorial_decision
    tools = read_stage_skills(app, task)
    result = EditorialDecision(
        candidate_ids=["a"], decision=decision, reason="该进展需要在明确条件满足后再作判断。"
    )
    with pytest.raises(ValueError, match="只能提交报道或不报道"):
        tools.submit_editorial_decision(result)
    with app.unit() as work:
        assert not work.stories.decisions()
        # 模拟升级前冻结的任务，验证旧协议及保存结果仍可读。
        work.tasks.save(work.tasks.get(task.id).model_copy(update={"binary_editorial_decision": False}))
    with pytest.raises(ValueError, match="重新查看条件"):
        tools.submit_editorial_decision(result)
    condition = "核实对照组的条件" if decision == "request_evidence" else "等待实验结果公开"
    assert (
        tools.submit_editorial_decision(result.model_copy(update={"revisit_condition": condition}))
        == decision
    )
    with app.unit() as work:
        assert not work.stories.list()
        saved = work.stories.decisions()[0].decision
        assert (saved.decision, saved.revisit_condition) == (decision, condition)


@pytest.mark.parametrize("field", ["angle", "boundary"])
def test_fact_check_accepts_exact_commission_requirement(production, field):
    app, _ = production
    story_id, _ = commission(app, boundary="实验结果仅适用于指定的体外条件。")
    submit_content(app)
    queued = pending(app, "reviewer")
    with app.unit() as work:
        work.tasks.save(queued.model_copy(update={"newsroom_policy": "news-focus-v2"}))
        requirement = getattr(work.stories.get(story_id).brief, field)
    task = app.execution.claim(queued.id)
    issue = ReviewIssue(
        id="gap",
        severity="required",
        category="requirement_gap",
        location="正文",
        reason="稿件遗漏了当前任务书的核心要求。",
        resolution="补充任务书中已有的核心要求。",
        requirement=requirement,
    )
    report = ReviewSubmission(
        decision="changes_requested", summary="稿件须按实际任务书要求返修。", issues=[issue]
    )
    invented = issue.model_copy(update={"requirement": "另外增加两个实验。"})
    with pytest.raises(ValueError, match="报道角度"):
        app.handoff.review(task.id, task.lease_token, report.model_copy(update={"issues": [invented]}))
    app.handoff.review(task.id, task.lease_token, report)
    with app.unit() as work:
        assert work.reviews.list()[0].submission.issues[0].requirement == requirement


def test_article_edit_conversation_accept_stale_and_restore(production):
    from agent4pharma.modules.content.article_editing import (
        ArticleEditRequest,
        ArticleEditResult,
        EditOption,
        TextChange,
        TextReference,
    )
    from agent4pharma.workflows.article_editing import (
        AcceptArticleEdit,
        accept_article_edit,
        article_edit_context,
        article_edit_state,
        queue_article_edit,
        submit_article_edit,
    )

    app, _ = production
    commission(app)
    submit_content(app)
    review(app)
    source = package(app)
    state = article_edit_state(app, source)
    original = state.draft
    text = original.submission.body
    start = text.index("需要结合")
    selected = text[start : start + 4]
    request = ArticleEditRequest(
        package_id=source,
        draft_id=original.id,
        request_id="one",
        instruction="这处自然一些",
        references=[TextReference(start=start, end=start + 4, text=selected)],
    )
    queued = queue_article_edit(app, request)
    assert queue_article_edit(app, request).id == queued.id
    task = app.execution.claim(queued.id)
    context = article_edit_context(app, task.id, task.lease_token)
    assert context.request.references[0].text == selected
    assert not hasattr(context, "evidence")
    result = ArticleEditResult(
        message="保留事实，调整表述。",
        options=[
            EditOption(
                label="直接一些",
                explanation="简化这一处句式，保留其余信息。",
                changes=[TextChange(start=start, end=start + 4, text=selected, replacement="应结合")],
                claims=original.submission.claims,
            )
        ],
    )
    submit_article_edit(app, task.id, task.lease_token, result)
    assert article_edit_state(app, source).draft.id == original.id
    assert app.next_task(app.execution.get(task.id)) is None
    new_task = accept_article_edit(
        app, AcceptArticleEdit(package_id=source, draft_id=original.id, task_id=task.id)
    )
    state = article_edit_state(app, source)
    assert state.pending_review and new_task.role == "reviewer"
    assert state.draft.submission.body == text[:start] + "应结合" + text[start + 4 :]
    assert len(state.versions) == 2 and state.versions[0] == original
    with pytest.raises(ValueError, match="版本已变化"):
        accept_article_edit(app, AcceptArticleEdit(package_id=source, draft_id=original.id, task_id=task.id))
    with pytest.raises(ValueError, match="失效|版本"):
        app.export_package(source)
    accept_article_edit(
        app, AcceptArticleEdit(package_id=source, draft_id=state.draft.id, restore_draft_id=original.id)
    )
    restored = article_edit_state(app, source)
    assert restored.draft.number == 3 and restored.draft.submission == original.submission
    assert app.execution.get(new_task.id).status == "cancelled"


def test_article_edit_ranges_keep_unicode_and_repeated_text():
    from agent4pharma.modules.content.article_editing import (
        EditOption,
        TextChange,
        TextReference,
        apply_option,
        validate_ranges,
    )

    text = "😀甲段相同。乙段相同。"
    start = text.rindex("相同")
    option = EditOption(
        label="局部",
        explanation="只改第二处",
        changes=[TextChange(start=start, end=start + 2, text="相同", replacement="不同")],
        claims=draft().claims,
    )
    assert apply_option(text, option) == "😀甲段相同。乙段不同。"
    with pytest.raises(ValueError):
        validate_ranges(text, [TextReference(start=0, end=2, text="错误")])
    with pytest.raises(ValueError):
        validate_ranges(
            text,
            [TextReference(start=1, end=3, text=text[1:3]), TextReference(start=2, end=4, text=text[2:4])],
        )


def test_integrated_search_requires_frozen_phase_role_permission_and_current_lease(production):
    app, _ = production
    enable_newsroom_pipeline(app)
    task = app.execution.claim(app.start(["a"], ProductionLimits()).id)
    with pytest.raises(ValueError, match="租约"):
        app.register_search_results(task.id, "wrong", [])
    app.execution.fail(task, "technical failure")
    retry = control_task(app, app.execution.get(task.id), "retry")
    assert retry.research_policy == "pitch-first-v1" and retry.web_search_enabled
    retried = app.execution.claim(retry.id)
    app.register_search_results(retried.id, retried.lease_token, [])
    for role, phase in [
        ("reviewer", "fact-check"),
        ("publisher", "layout-production"),
        ("writer", "topic-selection"),
    ]:
        with app.unit() as work:
            work.tasks.save(retried.model_copy(update={"role": role, "phase": phase}))
        with pytest.raises(ValueError, match="未开放"):
            app.register_search_results(retried.id, retried.lease_token, [])
    with app.unit() as work:
        work.tasks.save(retried.model_copy(update={"research_policy": "", "web_search_enabled": False}))
    with pytest.raises(ValueError, match="未开放"):
        app.register_search_results(retried.id, retried.lease_token, [])
    legacy = retried.model_copy(update={"research_policy": ""})
    assert "research_policy" not in legacy.model_dump(mode="json")


def test_integrated_editorial_mcp_can_save_background_but_not_author_research(production):
    app, root = production
    enable_newsroom_pipeline(app)
    first = app.execution.claim(app.start(["a"], ProductionLimits()).id)
    pitch = first
    source = ResearchEvidence(
        id="web-editorial",
        title="Background",
        text="An independently published background fact.",
        origin="web_search_snippet",
    )
    app.register_search_results(pitch.id, pitch.lease_token, [source])
    result = EditorialDecision(
        candidate_ids=["a"], decision="decline", reason="现有结果不足以支撑有新闻价值的角度。"
    )

    async def check():
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "agent4pharma",
                "production-mcp",
                "--root",
                str(root),
                "--task",
                pitch.id,
                "--lease",
                pitch.lease_token,
            ],
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert {
                    "read_evidence",
                    "read_document",
                    "find_bound_evidence",
                    "submit_editorial_decision",
                    "save_background_research",
                } <= names
                assert not (
                    {"submit_reporting", "save_writing_research", "search_local_materials", "submit_draft"}
                    & names
                )
                response = await session.call_tool("read_evidence", {"evidence_id": source.id})
                assert not response.is_error
                for resource in pitch.required_resources:
                    response = await session.call_tool("read_newsroom_resource", {"resource": resource})
                    assert not response.is_error
                saved = await session.call_tool(
                    "save_background_research", {"result": background_packet().model_dump(mode="json")}
                )
                assert not saved.is_error
                rejected = await session.call_tool(
                    "submit_editorial_decision",
                    {
                        "result": {
                            "candidate_ids": ["a"],
                            "decision": "defer",
                            "reason": "该进展需要等待更多公开结果才能判断。",
                        }
                    },
                )
                assert rejected.is_error
                assert "commission" in str(rejected.content) and "decline" in str(rejected.content)
                assert len(app.context(pitch.id, pitch.lease_token).background_research) == 1
                submitted = await session.call_tool(
                    "submit_editorial_decision", {"result": result.model_dump(exclude={"revisit_condition"})}
                )
                assert not submitted.is_error

    asyncio.run(check())
    with app.unit() as work:
        assert not work.stories.list()
        assert work.stories.decisions()[0].decision == result
        assert work.tasks.get(pitch.id).status == "succeeded"


@pytest.mark.parametrize("phase", ["topic-selection", "draft-writing"])
def test_native_search_event_registers_sources_for_integrated_roles(production, phase):
    from agent4pharma.infrastructure.llm.production_telemetry import ProductionTelemetry
    from agent4pharma.infrastructure.persistence.sqlite.article_regression_repository import (
        ObservationRepository,
    )

    app, root = production
    enable_newsroom_pipeline(app)
    task = app.execution.claim(app.start(["a"], ProductionLimits()).id)
    if phase == "draft-writing":
        read_stage_skills(app, task).submit_editorial_decision(
            EditorialDecision(
                candidate_ids=["a"], decision="commission", reason="A bounded report.", brief=brief()
            )
        )
        task = app.execution.claim(pending(app, "writer").id)
    meter = ProductionTelemetry(
        ObservationRepository(root),
        task.id,
        task.role,
        task.attempt,
        lambda evidence: app.register_search_results(task.id, task.lease_token, evidence),
    )
    data = {
        "message": {"toolCallId": "call-1", "isError": False},
        "meta": {
            "sources": [
                {
                    "url": "https://example.org/background",
                    "title": "Background",
                    "snippet": "A retrieved public background statement.",
                }
            ]
        },
    }
    meter.event({"type": "tool/result", "data": data})
    assert not any(e.origin == "web_search_snippet" for e in app.context(task.id, task.lease_token).evidence)
    meter.event({"type": "tool/call", "data": {"name": "web_search", "callId": "call-1"}})
    meter.event({"type": "tool/result", "data": data})
    evidence = next(
        e for e in app.context(task.id, task.lease_token).evidence if e.origin == "web_search_snippet"
    )
    assert not evidence.fulltext and evidence.text == "A retrieved public background statement."
    assert (
        ProductionAgentTools(app, task.id, task.lease_token).read_evidence(evidence.id).text == evidence.text
    )


@pytest.mark.parametrize("phase", ["draft-writing", "fact-check"])
def test_model_entry_reads_bound_fulltext_on_demand(production, monkeypatch, phase):
    from agent4pharma import composition
    from agent4pharma.infrastructure.llm.production_runner import DshProductionRunner
    from agent4pharma.modules.materials.document_reading import (
        DocumentPreparation,
        DocumentReadResult,
        DocumentReference,
    )

    app, root = production
    candidate = app.candidates(["a"])[0]
    pdf = ResearchEvidence(
        id="pdf",
        title="Paper",
        text="Document catalog only",
        origin="primary_document",
        document=DocumentReference(id="download", sha256="b" * 64, pages=10),
    )
    app.candidates = lambda ids: [candidate.model_copy(update={"evidence": [*candidate.evidence, pdf]})]
    commission(app)
    if phase == "fact-check":
        submit_content(app)
    queued = pending(app, "writer" if phase == "draft-writing" else "reviewer")
    with app.unit() as work:
        work.tasks.save(queued.model_copy(update={"phase": phase, "newsroom_policy": "news-focus-v2"}))
    preparations = []

    def prepare(task_id):
        preparations.append(task_id)
        return [DocumentPreparation(sha256="b" * 64, status="unavailable", error_code="server_not_running")]

    app.prepare_documents = prepare
    monkeypatch.setattr(composition, "build_production_workbench", lambda _: app)
    entered = []
    reads = []

    def read_pages(ref, start, end, after):
        reads.append((ref.id, start, end))
        return DocumentReadResult(status="unavailable", error_code="server_not_running")

    def cached_pages(ref, start, end, after):
        return DocumentReadResult(status="pending", error_code="document_not_prepared")

    def run(self, task):
        entered.append(task.id)
        response = app.read_document(task.id, task.lease_token, pdf.id, 2, 3)
        assert response.status == "unavailable"

    app.document_reader = read_pages
    app.cached_document_reader = cached_pages
    monkeypatch.setattr(DshProductionRunner, "run", run)
    result = composition.execute_production_task(root, queued.id)
    assert entered == [queued.id] and not preparations
    assert result.attempt == 1 and result.document_reads
    assert reads == [("download", 2, 3)]


def test_content_requeue_is_rejected_without_changing_story(production):
    app, _ = production
    story, _ = commission(app)
    before = app.snapshot().model_dump(exclude={"as_of"})
    with pytest.raises(ValueError, match="准确任务"):
        app.resume(story, "writer", ProductionLimits())
    api = ProductionAPI(app, app.execution.get, lambda: False)
    try:
        with pytest.raises(ValueError):
            api.post("/api/production/resume", {"story_id": story, "role": "writer"})
        assert app.snapshot().model_dump(exclude={"as_of"}) == before
    finally:
        api.close()


def test_automatic_dispatch_failure_is_terminal_and_redacted(production):
    app, _ = production

    def broken(identity):
        raise RuntimeError("private provider diagnostic")

    api = ProductionAPI(app, broken, lambda: True)
    try:
        queued = api.post("/api/production/start", {"candidate_ids": ["a"]})
        api.executor.shutdown(wait=True)
        task = app.execution.get(queued["id"])
        assert task.status == "failed" and task.auto_run
        assert "RuntimeError" in task.error and "private" not in task.error
        assert not api.pending
    finally:
        api.close()


def test_restart_resumes_only_requested_queue_and_ends_interrupted_attempt(production):
    app, _ = production
    old = app.start(["a"], ProductionLimits())
    automatic = app.execution.request_automatic(app.start(["b"], ProductionLimits()).id)
    api = deferred_production_api(app)
    try:
        api.resume_automatic()
        assert api.pending == {automatic.id}
        assert app.execution.get(old.id) == old
        running = app.execution.claim(automatic.id)
        api.resume_automatic()
        failed = app.execution.get(running.id)
        assert failed.status == "failed" and "重启" in failed.error and not failed.lease_token
        assert not app.execution.is_current(running)
    finally:
        api.close()


def test_workbench_startup_does_not_derive_model_work_from_recommendations(production, monkeypatch):
    from types import SimpleNamespace

    from agent4pharma.entrypoints.web import server as server_module
    from agent4pharma.infrastructure.persistence.paper_download_repository import PaperDownloadRepository
    from agent4pharma.infrastructure.persistence.paper_selection_repository import PaperSelectionRepository
    from agent4pharma.workflows import selected_paper_download

    app, root = production
    queued = app.start(["a"], ProductionLimits())
    item = SimpleNamespace(
        rank=1,
        download_id="prepared",
        human_recommendation=object(),
        continuation_status="idle",
        request=SimpleNamespace(evidence=[SimpleNamespace(id="paper")]),
    )
    run = SimpleNamespace(
        id="selection", batch_id="batch", phase="fulltexts", paused=False, shortlist_limit=5, items=[item]
    )
    monkeypatch.setattr(PaperSelectionRepository, "latest", lambda *_: [run])
    monkeypatch.setattr(PaperDownloadRepository, "verified", lambda *_: object())
    monkeypatch.setattr(selected_paper_download, "download_request", lambda *_: None)
    monkeypatch.setattr(server_module, "build_production_workbench", lambda *_: app)
    continuation = SimpleNamespace(
        start=lambda *_: pytest.fail("Startup must not request models"),
        close=lambda: None,
        confirm_import=lambda *_: None,
    )
    monkeypatch.setattr(server_module, "build_paper_continuation", lambda *_, **__: continuation)

    class StartupServer:
        server_port = 18999

        def __init__(self, *args):
            pass

        def serve_forever(self):
            assert app.snapshot().tasks == [queued]

        def server_close(self):
            pass

    monkeypatch.setattr(server_module, "WorkbenchHTTPServer", StartupServer)
    server_module.run_workbench(root / "settings.toml", ApplicationSettings(data_dir=root))
    assert app.execution.get(queued.id) == queued


def test_manual_import_and_production_share_one_serial_model_executor(production):
    from agent4pharma.entrypoints.web.paper_continuation_api import PaperContinuationAPI

    app, _ = production
    entered, release, imported = threading.Event(), threading.Event(), threading.Event()

    def execute(identity):
        entered.set()
        assert release.wait(5)
        return app.execution.fail_interrupted(identity, "offline terminal test result")

    api = ProductionAPI(app, execute, lambda: True)
    continuation = PaperContinuationAPI(
        lambda *_: "idle", lambda *_: None, lambda *_: imported.set(), executor=api.executor
    )
    try:
        api.post("/api/production/start", {"candidate_ids": ["a"]})
        assert entered.wait(2)
        continuation.start("run", "paper")
        assert not imported.is_set()
        release.set()
        api.executor.shutdown(wait=True)
        assert imported.is_set() and not api.pending
    finally:
        release.set()
        continuation.close()
        api.close()


@pytest.mark.parametrize("action", ["supply", "resume-research"])
def test_cli_does_not_offer_retired_manual_evidence_commands(action):
    import argparse

    from agent4pharma.entrypoints.cli.production_commands import add_production_arguments

    parser = argparse.ArgumentParser()
    add_production_arguments(parser.add_subparsers(dest="command"))
    with pytest.raises(SystemExit):
        parser.parse_args(["production", action])
    assert parser.parse_args(["production", "resume", "story"]).role == "reviewer"
    with pytest.raises(SystemExit):
        parser.parse_args(["production", "resume", "story", "--role", "writer"])


def test_task_details_return_only_the_exact_committed_business_artifact(production):
    app, _ = production
    story, editorial_task = commission(app)
    writer = pending(app, "writer")
    draft_id, _ = submit_content(app)
    reviewer = pending(app, "reviewer")
    review(app)
    publisher = pending(app, "publisher")
    package_id = package(app)
    api = ProductionAPI(app, lambda _: pytest.fail("read-only query"), lambda: False)
    try:

        def output(task):
            return api.get(f"/api/production/tasks/{task.id}")["artifact"]

        decision = output(editorial_task)
        assert decision["kind"] == "decision"
        original_title = decision["content"]["brief"]["title"]
        assert output(writer)["kind"] == "draft" and output(writer)["content"]["id"] == draft_id
        assert output(reviewer)["kind"] == "review"
        assert output(publisher)["kind"] == "package" and output(publisher)["content"]["id"] == package_id
        with app.unit() as work:
            saved = work.stories.get(story)
            work.stories.save(
                saved.model_copy(
                    update={
                        "brief": saved.brief.model_copy(update={"title": "A later title"}),
                        "revision": saved.revision + 1,
                    }
                ),
                saved.revision,
            )
            old_draft = work.drafts.get(draft_id)
            work.drafts.save(old_draft.model_copy(update={"id": "later-draft", "number": 2}))
        assert output(editorial_task)["content"]["brief"]["title"] == original_title
        assert output(writer)["content"]["id"] == draft_id
        with app.unit() as work:
            saved_task = work.tasks.get(writer.id)
            work.tasks.save(saved_task.model_copy(update={"result_ref": "missing-artifact"}))
        assert output(writer) is None
    finally:
        api.close()


def test_remove_legacy_orphan_backs_up_and_keeps_other_tasks_and_candidates(production):
    from agent4pharma.infrastructure.persistence.sqlite.article_regression_repository import (
        ObservationRepository,
    )
    from agent4pharma.infrastructure.persistence.sqlite.legacy_production_cleanup import (
        remove_legacy_production_task,
    )
    from agent4pharma.runtime.workflow_observation import WorkflowObservation

    app, root = production
    running = app.execution.claim(app.start(["a"], ProductionLimits()).id)
    app.execution.fail(running, "legacy failure")
    other = app.start(["b"], ProductionLimits())
    observations = ObservationRepository(root)
    observations.append(
        WorkflowObservation(
            id="old-event",
            subject_ref=running.id,
            stage="editor",
            operation="model",
            status="failed",
            created_at="2026-11-01T00:00:00+00:00",
        )
    )
    observations.append(
        WorkflowObservation(
            id="other-event",
            subject_ref=other.id,
            stage="editor",
            operation="model",
            status="started",
            created_at="2026-11-01T00:00:00+00:00",
        )
    )
    backup = root / "backups" / "before-orphan.sqlite3"
    result = remove_legacy_production_task(root, running.id, backup)
    assert (result.tasks, result.observations) == (1, 1)
    with pytest.raises(KeyError):
        app.execution.get(running.id)
    assert app.execution.get(other.id) == other
    assert len(app.candidates(["a"])) == 1
    assert observations.list([running.id]) == []
    assert len(observations.list([other.id])) == 1
    with sqlite3.connect(backup) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM production_tasks WHERE id=?", (running.id,)).fetchone()[0] == 1
        )
    again = remove_legacy_production_task(root, running.id, root / "backups" / "again.sqlite3")
    assert again.tasks == 0


@pytest.mark.parametrize("referenced", [False, True])
def test_legacy_orphan_cleanup_rejects_live_or_referenced_tasks(production, referenced):
    from agent4pharma.infrastructure.persistence.sqlite.legacy_production_cleanup import (
        remove_legacy_production_task,
    )

    app, root = production
    task = app.start(["a"], ProductionLimits())
    if referenced:
        running = app.execution.claim(task.id)
        app.execution.fail(running, "legacy failure")
        other = app.start(["b"], ProductionLimits())
        with app.unit() as work:
            work.tasks.save(other.model_copy(update={"parent_task_id": task.id}))
    before = app.snapshot().model_dump(exclude={"as_of"})
    backup = root / "backups" / "not-created.sqlite3"
    with pytest.raises(ValueError):
        remove_legacy_production_task(root, task.id, backup)
    assert not backup.exists()
    assert app.snapshot().model_dump(exclude={"as_of"}) == before


def test_paper_remake_queues_fresh_root_with_current_prompts_and_keeps_original(production):
    from uuid import uuid4

    from agent4pharma.workflows.newsroom_skills import replace_skill_resources
    from agent4pharma.workflows.production_dependencies import ProductionContext

    app, _ = production
    candidate = app.candidates(["a"])[0].model_copy(
        update={"id": "human-paper-" + "a" * 32, "assessment_id": ""}
    )
    app.candidates = lambda ids: [candidate] if ids == [candidate.id] else []
    enable_newsroom_pipeline(app)
    bundle = load_newsroom_skills("editor")
    app.definitions["editor"] = app.definitions["editor"].model_copy(
        update={
            "newsroom_pipeline": True,
            "newsroom_skills": replace_skill_resources(bundle, {"standards/audience.md": "Old audience"}),
        }
    )
    original = app.start([candidate.id], ProductionLimits())
    original_context = original.input_json
    app.definitions["editor"] = app.definitions["editor"].model_copy(
        update={
            "prompt": "Current published prompt",
            "newsroom_skills": replace_skill_resources(bundle, {"standards/audience.md": "Current audience"}),
        }
    )
    api = deferred_production_api(app)
    payload = {"candidate_id": candidate.id, "request_id": str(uuid4())}
    try:
        fresh = api.post("/api/production/remake", payload)
        task = app.execution.get(fresh["id"])
        assert task.id != original.id and task.status == "queued" and task.attempt == 0
        assert task.phase == "topic-selection" and not task.parent_task_id and not task.story_id
        assert task.prompt.startswith("Current published prompt")
        assert (
            next(r.text for r in task.newsroom_skills.resources if r.name == "standards/audience.md")
            == "Current audience"
        )
        context = ProductionContext.model_validate_json(task.input_json)
        assert context.remake_of_task_id == original.id and context.remake_request_id == payload["request_id"]
        assert not context.story and not context.draft and not context.reviews
        assert task.limits == ProductionLimits.for_new_task()
        assert app.execution.get(original.id) == original
        assert app.execution.get(original.id).input_json == original_context
        app.definitions["editor"] = app.definitions["editor"].model_copy(
            update={"prompt": "Even newer prompt"}
        )
        assert api.post("/api/production/remake", payload)["id"] == task.id
        with pytest.raises(ValueError, match="冻结其他返修额度"):
            api.post("/api/production/remake", {**payload, "max_revision_rounds": 10})
        another = api.post("/api/production/remake", {**payload, "request_id": str(uuid4())})
        assert another["id"] != task.id
        assert app.execution.get(another["id"]).prompt.startswith("Even newer prompt")
        ten = api.post(
            "/api/production/remake", {**payload, "request_id": str(uuid4()), "max_revision_rounds": 10}
        )
        assert app.execution.get(ten["id"]).limits.max_revision_rounds == 10
        assert len(app.snapshot().paper_flows) == 4
        history = api.get("/api/production/remakes?candidate=" + candidate.id)["items"]
        assert {row["task_id"] for row in history} == {task.id, another["id"], ten["id"]}
        assert api.get("/api/production/remakes?candidate=other")["items"] == []
        before = app.snapshot()
        status = api.get("/api/production/paper-status")
        assert len(status["paper_flows"]) == 4
        assert {row["id"] for row in status["tasks"]} == {original.id, task.id, another["id"], ten["id"]}
        assert all(row["status"] == "queued" for row in status["tasks"])
        assert all("input_json" not in row and "prompt" not in row for row in status["tasks"])
        assert app.snapshot().tasks == before.tasks
        assert app.snapshot().paper_flows == before.paper_flows
    finally:
        api.close()


@pytest.mark.parametrize("legacy_event", [False, True])
def test_remake_can_submit_editorial_without_colliding_with_operator_event(production, legacy_event):
    from uuid import uuid4

    from agent4pharma.workflows.production_remake import RemakeProduction, remake_paper

    app, root = production
    enable_newsroom_pipeline(app)
    candidate = app.candidates(["a"])[0].model_copy(update={"id": "human-paper-" + "a" * 32})
    app.candidates = lambda ids: [candidate] if ids == [candidate.id] else []
    original = app.start([candidate.id], ProductionLimits())
    fresh = remake_paper(app, RemakeProduction(candidate_id=candidate.id, request_id=uuid4()))
    remake_id = fresh.id + ":remake"
    if legacy_event:
        with sqlite3.connect(root / "discovery.sqlite3") as connection:
            connection.execute("UPDATE production_operator_events SET id=? WHERE id=?", (fresh.id, remake_id))
        remake_id = fresh.id
    running = app.execution.claim(fresh.id)
    tools = read_stage_skills(app, running)
    packet = EditorialDecision(
        candidate_ids=[candidate.id], decision="commission", reason="A bounded report.", brief=brief()
    )
    tools.submit_editorial_decision(packet)
    saved = app.execution.get(fresh.id)
    assert saved.status == "succeeded"
    successor = app.next_task(saved)
    assert successor.parent_task_id == fresh.id and successor.status == "queued"
    assert app.execution.get(original.id) == original
    with sqlite3.connect(root / "discovery.sqlite3") as connection:
        events = dict(connection.execute("SELECT id,action FROM production_operator_events"))
    assert events == {remake_id: "remake", fresh.id + ":completed": "editor"}
    with pytest.raises(ValueError, match="租约"):
        tools.submit_editorial_decision(packet)


def test_paper_remake_rejects_missing_recommendation_and_unknown_production(production):
    from uuid import uuid4

    from agent4pharma.workflows.production_remake import RemakeProduction, remake_paper

    app, _ = production
    candidate = app.candidates(["a"])[0].model_copy(update={"id": "human-paper-" + "b" * 32})
    app.candidates = lambda _: [candidate]
    app.definitions["writer"] = app.definitions["writer"].model_copy(update={"newsroom_pipeline": True})
    request = RemakeProduction(candidate_id=candidate.id, request_id=uuid4())
    with pytest.raises(ValueError, match="尚未开始"):
        remake_paper(app, request)
    app.candidates = lambda _: []
    with pytest.raises(ValueError, match="失效"):
        remake_paper(app, request)
    with pytest.raises(ValueError):
        RemakeProduction(candidate_id="unconfirmed", request_id=uuid4())
    with pytest.raises(ValueError):
        RemakeProduction.model_validate(
            {"candidate_id": candidate.id, "request_id": str(uuid4()), "allow_model": True}
        )
    assert not app.snapshot().tasks


def test_total_revision_budget_bounds_role_workflow_without_phase_parents(production):
    app, _ = production
    story_id, _ = commission(app)
    initial = pending(app, "writer")
    with app.unit() as work:
        work.tasks.save(
            initial.model_copy(
                update={"limits": initial.limits.model_copy(update={"max_revision_rounds": 5})}
            )
        )
    submit_content(app)
    issue = ReviewIssue(
        id="required-change",
        severity="required",
        location="正文",
        reason="此离线问题用于验证无阶段父链时的总返修额度。",
        resolution="作者须回应并提交一个修订版本。",
    )
    for index in range(6):
        task = app.execution.claim(pending(app, "reviewer").id)
        assert not task.phase
        app.handoff.review(
            task.id,
            task.lease_token,
            ReviewSubmission(
                decision="changes_requested", summary="离线验证返修额度，不代表模型质量判断。", issues=[issue]
            ),
        )
        if index == 5:
            break
        assert pending(app, "writer").limits.max_revision_rounds == 5
        submit_content(app, resolved_issue_ids=[issue.id])
    with app.unit() as work:
        assert work.stories.get(story_id).status == "deferred"
        assert len(work.drafts.list()) == 6
        assert not any(t.status == "queued" for t in work.tasks.list())


def test_background_page_binding_cache_and_research_handoff(production):
    from agent4pharma.modules.content.draft_contracts import EvidenceSpan
    from agent4pharma.modules.content.research_contracts import ResearchFinding

    app, _ = production
    writer, tools = commission_writer(app)
    url = "https://example.org/commentary"
    snippet = ResearchEvidence(
        id="search-hit", title="Commentary", text="Search summary", url=url, origin="web_search_snippet"
    )
    app.register_search_results(writer.id, writer.lease_token, [snippet])
    calls = []
    page = ResearchEvidence(
        id="page-body",
        title="Commentary",
        text="This is an external interpretation of the result.",
        url=url,
        origin="primary_page",
    )

    def read_page(address):
        calls.append(address)
        return page

    app.reader = read_page
    with pytest.raises(ValueError, match="已登记"):
        tools.read_bound_source("https://example.org/unbound")
    assert calls == []
    first = tools.read_bound_source(url)[0]
    assert first.origin == "web_background_page"
    reads = app.execution.get(writer.id).reads
    assert tools.read_bound_source(url)[0] == first and calls == [url]
    assert app.execution.get(writer.id).reads == reads
    finding = ResearchFinding(
        kind="background",
        statement=page.text,
        evidence_id=page.id,
        source_span=EvidenceSpan(content_hash=page.content_hash, start=0, end=len(page.text)),
        narrative_use="context",
        selection_reason="解释意义，不作为独立实验验证。",
        source_relation="external_commentary",
    )
    invalid = writing_packet().model_copy(
        update={"findings": [finding.model_copy(update={"kind": "finding"})]}
    )
    with pytest.raises(ValueError, match="background"):
        tools.save_writing_research(invalid)
    packet = writing_packet().model_copy(
        update={"background": [finding], "claim_check": "主张只限于原论文给定实验条件，评论未提供独立验证。"}
    )
    saved = tools.save_writing_research(packet)
    assert tools.save_writing_research(packet) == saved
    tools.submit_draft(draft())
    editor = app.execution.claim(pending(app, "reviewer").id)
    context = app.context(editor.id, editor.lease_token)
    assert context.writing_research == saved
    assert any(e.id == page.id and e.origin == "web_background_page" for e in context.evidence)
    with pytest.raises(ValueError, match="租约"):
        tools.read_bound_source(url)


def test_emphasis_submission_without_figures_preserves_draft(production):
    from agent4pharma.modules.publishing.article_layout import ArticleEmphasis, LayoutRequest
    from agent4pharma.modules.publishing.article_renderer import text_hash

    app, _ = production
    commission(app)
    submit_content(app)
    review(app)
    task = app.execution.claim(pending(app, "publisher").id)
    tools = ProductionAgentTools(app, task.id, task.lease_token)
    current = tools.get_production_context().draft
    emphasis = ArticleEmphasis(
        source_body_hash=current.source_body_hash,
        items=[{"paragraph": 1, "quote": "Observed activity was 10%."}],
    )
    identity = tools.submit_channel_package(
        ChannelSubmission(
            channel="local_html", release_note="准确正文重点标注，无模型改写或外部发布。", emphasis=emphasis
        )
    )
    with app.unit() as work:
        result = next(p for p in work.releases.packages() if p.id == identity)
        assert work.drafts.get(result.draft_id).submission.body == current.submission.body
        assert result.presentation.emphasis == emphasis
        assert "<strong" in result.body and "<strong" in result.presentation.wechat_html
    request = LayoutRequest(package_id=identity, emphasis=emphasis)
    derived = app.create_layout(request)
    assert app.create_layout(request).id == derived.id
    with pytest.raises(ValueError, match="版本"):
        app.create_layout(
            request.model_copy(
                update={"emphasis": emphasis.model_copy(update={"source_body_hash": text_hash("stale")})}
            )
        )
    approve_current(app, derived.id)
    assert "<strong" in app.export_package(derived.id, "wechat")[0]


def test_legacy_patch_revision_preserves_unedited_text_and_requires_new_review(production):
    from agent4pharma.modules.content.draft_revision import DraftRevisionSubmission, RevisionChange
    from agent4pharma.modules.review.reader_review_contracts import (
        ReaderObservation,
        ReaderReviewResult,
        ReaderRevisionIssue,
    )
    from agent4pharma.workflows.revision_context import is_patch_revision

    app, _ = production
    writer, tools = commission_writer(app)
    # 历史链仍冻结v1接口；新的候选工作区另有完整业务场景回归。
    with app.unit() as work:
        saved = work.tasks.get(writer.id)
        work.tasks.save(saved.model_copy(update={"context_contract": "node-context-v1"}))
    tools.save_writing_research(writing_packet())
    original_id = tools.submit_draft(draft())
    editor = app.execution.claim(pending(app, "reviewer").id)
    review_tools = read_stage_skills(app, editor)
    review_tools.get_reader_draft()
    review_tools.save_reader_observation(
        ReaderObservation(
            news_understood="The reported activity is bounded by assay conditions.",
            reason_to_read="Understand an experiment.",
            first_obstacle="Repeated explanation.",
        )
    )
    review_tools.get_reader_brief()
    review_tools.submit_reader_review(
        ReaderReviewResult(
            decision="changes_requested",
            summary="Clarify the title for readers.",
            appeal_and_flow="completed",
            language_naturalness="completed",
            issues=[
                ReaderRevisionIssue(
                    id="title",
                    dimension="appeal_and_flow",
                    severity="required",
                    anchors=["证据解读"],
                    problem="The title does not describe the measured event.",
                    reader_impact="Readers cannot understand the subject from the title.",
                    resolution="Name the reported activity and retain its scope.",
                )
            ],
        )
    )
    queued = pending(app, "writer")
    assert is_patch_revision(queued)
    revised = app.execution.claim(queued.id)
    revision_tools = ProductionAgentTools(app, revised.id, revised.lease_token)
    context = revision_tools.get_revision_context()
    assert context.draft.id == original_id
    assert len(context.resources) == len(revised.required_resources)
    assert not {"evidence", "reader_reviews", "background_research"} & type(context).model_fields.keys()
    assert set(app.execution.get(revised.id).skill_reads) == set(revised.required_resources)
    payload = DraftRevisionSubmission(
        draft_id=original_id,
        content_hash=context.draft.content_hash,
        title="实验报告了限定条件下的活性",
        coverage="仅修订标题，正文及证据保持原样。",
        resolved_issue_ids=["title"],
        reader_responses=[{"issue_id": "title", "action": "revised", "reason": "标题写出实验观察及条件。"}],
    )
    with pytest.raises(ValueError, match="准确稿件"):
        revision_tools.submit_draft_revision(payload.model_copy(update={"content_hash": "0" * 64}))
    assert app.execution.get(revised.id).status == "running"
    with pytest.raises(ValueError, match="不存在或不唯一"):
        revision_tools.submit_draft_revision(
            payload.model_copy(update={"changes": [RevisionChange(original="不存在", replacement="new")]})
        )
    with pytest.raises(ValueError, match="逐项回应"):
        revision_tools.submit_draft_revision(payload.model_copy(update={"resolved_issue_ids": []}))
    new_id = revision_tools.submit_draft_revision(payload)
    with app.read_unit() as work:
        old, new = work.drafts.get(original_id), work.drafts.get(new_id)
        assert old.submission.body == new.submission.body
        assert old.submission.claims == new.submission.claims
        assert old.content_hash != new.content_hash
    next_review = pending(app, "reviewer")
    assert next_review.phase == "reading-review"
    assert ProductionContext.model_validate_json(next_review.input_json).draft.id == new_id
    with pytest.raises(ValueError, match="租约"):
        revision_tools.submit_draft_revision(payload)


@pytest.mark.parametrize("reader_pipeline", [False, True])
def test_product_edit_applies_exact_package_then_restores_and_verifies(production, reader_pipeline):
    from agent4pharma.modules.content.article_editing import (
        ArticleEditRequest,
        ArticleEditResult,
        EditOption,
        TextChange,
    )
    from agent4pharma.workflows.article_editing import (
        article_edit_state,
        find_article_text,
        queue_article_edit,
        submit_article_edit,
    )
    from agent4pharma.workflows.article_product_editing import (
        ProductEditAction,
        restore_product,
    )

    app, _ = production
    commission(app)
    submit_content(app)
    review(app)
    source = package(app)
    if reader_pipeline:
        enable_newsroom_pipeline(app)
    initial = article_edit_state(app, source, True)
    request = ArticleEditRequest(
        package_id=source,
        draft_id=initial.draft.id,
        request_id="product-one",
        instruction="标题更明确",
        auto_apply=True,
    )
    queued = queue_article_edit(app, request)
    assert queued.web_search_enabled
    assert queue_article_edit(app, request).id == queued.id
    task = app.execution.claim(queued.id)
    read_stage_skills(app, task)
    refs = find_article_text(app, task.id, task.lease_token, "证据", "title")
    assert refs[0].target == "title"
    # 已完成选题允许按需读取绑定论文；搜索结果必须经过真实宿主登记接口。
    assert app.read_evidence(task.id, task.lease_token, "paper-v1").text
    app.register_search_results(task.id, task.lease_token, [])
    result = ArticleEditResult(
        message="标题已调整，其他内容保留。",
        options=[
            EditOption(
                label="定点修改",
                explanation="明确标题含义。",
                changes=[TextChange(target="title", start=0, end=2, text="证据", replacement="研究证据")],
            )
        ],
    )
    identity = submit_article_edit(app, task.id, task.lease_token, result)
    assert queue_article_edit(app, request).id == task.id
    changed = article_edit_state(app, identity, True)
    assert changed.package.edit_parent_id == source
    assert changed.pending_review and changed.editable
    assert changed.draft.submission.title == "研究证据解读"
    assert changed.draft.submission.body == initial.draft.submission.body
    assert not article_edit_state(app, source, True).editable
    assert article_edit_state(app, source, True).draft.id == initial.draft.id
    assert not [t for t in app.snapshot().tasks if t.status == "queued"]
    approve_current(app, identity)
    assert app.export_package(identity)[0] == changed.package.body
    restored = restore_product(app, ProductEditAction(package_id=identity, restore_package_id=source))
    restored_state = article_edit_state(app, restored.id, True)
    assert restored_state.draft.submission == initial.draft.submission
    assert restored_state.draft.number == initial.draft.number + 2
    with pytest.raises(ValueError):
        app.export_package(restored.id)
    approve_current(app, restored.id)
    assert app.export_package(restored.id)[0] == restored.body
    assert not [t for t in app.snapshot().tasks if t.status == "queued"]


def test_manual_article_save_is_offline_idempotent_and_preserves_review_boundary(production):
    from agent4pharma.modules.content.article_editing import ManualArticleEdit
    from agent4pharma.workflows.article_editing import article_edit_state
    from agent4pharma.workflows.article_product_editing import ProductEditAction, restore_product
    from agent4pharma.workflows.manual_article_editing import save_manual_article

    app, _ = production
    commission(app)
    submit_content(app)
    review(app)
    source = package(app)
    initial = article_edit_state(app, source, True)
    request = ManualArticleEdit(
        package_id=source,
        draft_id=initial.draft.id,
        title="人工修改后的标题",
        body=initial.draft.submission.body + "\n\n这是手工补充的说明。",
    )
    tasks = app.snapshot().tasks
    api = ProductionAPI(app, lambda _: pytest.fail("manual save must not run models"), lambda: False)
    try:
        result = api.post("/api/production/edit-manual", request.model_dump())
    finally:
        api.close()
    changed = article_edit_state(app, result["id"], True)
    assert changed.draft.submission.title == request.title
    assert changed.draft.submission.body == request.body
    assert changed.draft.author_id.startswith("human-manual:")
    assert changed.package.edit_parent_id == source and changed.package.edit_pending
    assert not changed.package.review_id
    assert app.snapshot().tasks == tasks
    assert save_manual_article(app, request).id == result["id"]
    with pytest.raises(ValueError, match="版本已变化"):
        save_manual_article(app, request.model_copy(update={"title": "过期窗口"}))
    with pytest.raises(ValueError, match="请先.*批准"):
        app.export_package(result["id"])
    approve_current(app, result["id"])
    assert app.export_package(result["id"])[0] == changed.package.body
    assert app.snapshot().tasks == tasks
    restored = restore_product(app, ProductEditAction(package_id=result["id"], restore_package_id=source))
    assert article_edit_state(app, restored.id, True).draft.submission == initial.draft.submission


def test_manual_article_rejects_wrong_draft_busy_and_changed_claim(production):
    from agent4pharma.modules.content.article_editing import ArticleEditRequest, ManualArticleEdit
    from agent4pharma.workflows.article_editing import article_edit_state, queue_article_edit
    from agent4pharma.workflows.manual_article_editing import save_manual_article

    app, _ = production
    commission(app)
    submit_content(app)
    review(app)
    source = package(app)
    initial = article_edit_state(app, source, True)
    request = ManualArticleEdit(
        package_id=source,
        draft_id=initial.draft.id,
        title="人工新标题",
        body=initial.draft.submission.body,
    )
    with pytest.raises(ValueError, match="稿件与图文"):
        save_manual_article(app, request.model_copy(update={"draft_id": "wrong"}))
    with pytest.raises(ValueError, match="没有变化"):
        save_manual_article(app, request.model_copy(update={"title": initial.draft.submission.title}))
    claim = initial.draft.submission.claims[0].claim
    new_body = request.body.replace(claim, "人工调整后的准确表述")
    with pytest.raises(ValueError, match="正文原句"):
        save_manual_article(app, request.model_copy(update={"body": new_body}))
    queue_article_edit(
        app,
        ArticleEditRequest(
            package_id=source,
            draft_id=initial.draft.id,
            request_id="busy-manual",
            instruction="调整标题",
        ),
    )
    with pytest.raises(ValueError, match="任务执行中"):
        save_manual_article(app, request)
    with app.unit() as work:
        assert len(work.releases.packages()) == 1


def test_manual_article_preserves_image_bytes_and_explicit_claim_mapping(production):
    from agent4pharma.modules.content.article_editing import ManualArticleEdit, ManualFigureText
    from agent4pharma.modules.materials.document_reading import DocumentReference
    from agent4pharma.modules.publishing.article_layout import FigureImage, FigureSelection, LayoutRequest
    from agent4pharma.workflows.article_editing import article_edit_state
    from agent4pharma.workflows.manual_article_editing import save_manual_article

    app, _ = production
    story, _ = commission(app)
    submit_content(app)
    review(app)
    source = package(app)
    with app.unit() as work:
        evidence = work.drafts.evidence(story)[0]
        work.drafts.add_evidence(
            story,
            evidence.model_copy(
                update={
                    "id": "manual-image",
                    "document": DocumentReference(id="bound", sha256="b" * 64, pages=2),
                }
            ),
        )
    app.figure_reader = lambda *_: FigureImage(b"manual-png", 200, 300, "p1b1")
    original = app.create_layout(
        LayoutRequest(
            package_id=source,
            figures=[
                FigureSelection(
                    evidence_id="manual-image",
                    page=1,
                    block=1,
                    after_paragraph=1,
                    label="原图",
                    caption="原图注",
                )
            ],
        )
    )
    app.figure_reader = lambda *_: pytest.fail("manual save must reuse original image bytes")
    initial = article_edit_state(app, original.id, True)
    claim = initial.draft.submission.claims[0].claim
    text = "人工修改后的关键表述"
    request = ManualArticleEdit(
        package_id=original.id,
        draft_id=initial.draft.id,
        title="手工改字标题",
        body=initial.draft.submission.body.replace(claim, text) + "\n\n新增一段文字。",
        claim_texts=[text] + [c.claim for c in initial.draft.submission.claims[1:]],
        figures=[ManualFigureText(caption="手工修改后的图注", after_paragraph=2)],
    )
    with pytest.raises(ValueError, match="图片位置"):
        save_manual_article(
            app,
            request.model_copy(
                update={
                    "figures": [ManualFigureText(caption="图注", after_paragraph=999)],
                }
            ),
        )
    with pytest.raises(ValueError, match="图片记录"):
        save_manual_article(app, request.model_copy(update={"figures": []}))
    saved = save_manual_article(app, request)
    figure = saved.presentation.figures[0]
    assert figure.image_sha256 == original.presentation.figures[0].image_sha256
    assert figure.selection.caption == "手工修改后的图注" and figure.selection.after_paragraph == 2
    changed = article_edit_state(app, saved.id, True)
    assert changed.draft.submission.claims[0].claim == text
    assert changed.draft.submission.claims[0].quote == initial.draft.submission.claims[0].quote
    approve_current(app, saved.id)
    assert app.export_package(saved.id, "wechat")[0] == saved.presentation.wechat_html


def test_manual_article_rechecks_revision_after_render(production, monkeypatch):
    from agent4pharma.modules.content.article_editing import ManualArticleEdit
    from agent4pharma.workflows import manual_article_editing
    from agent4pharma.workflows.article_editing import article_edit_state

    app, _ = production
    commission(app)
    submit_content(app)
    review(app)
    source = package(app)
    initial = article_edit_state(app, source, True)
    render = manual_article_editing.render_edited_product

    def concurrent_render(*args):
        result = render(*args)
        with app.unit() as work:
            story = work.stories.get(initial.package.story_id)
            work.stories.save(story.model_copy(update={"revision": story.revision + 1}), story.revision)
        return result

    monkeypatch.setattr(manual_article_editing, "render_edited_product", concurrent_render)
    with pytest.raises(ValueError, match="上下文已变化"):
        manual_article_editing.save_manual_article(
            app,
            ManualArticleEdit(
                package_id=source,
                draft_id=initial.draft.id,
                title="并发改字",
                body=initial.draft.submission.body,
            ),
        )
    with app.unit() as work:
        assert len(work.releases.packages()) == 1
        assert work.drafts.list()[-1].id == initial.draft.id


def test_human_edited_approval_keeps_version_and_integrity_guards(production):
    from agent4pharma.modules.content.article_editing import ArticleEditRequest, ManualArticleEdit
    from agent4pharma.modules.publishing.human_approval import ApprovePackage, package_fingerprint
    from agent4pharma.workflows.article_editing import article_edit_state, queue_article_edit
    from agent4pharma.workflows.human_package_approval import approve_package, require_reviewable_package
    from agent4pharma.workflows.manual_article_editing import save_manual_article

    app, _ = production
    commission(app)
    submit_content(app)
    review(app)
    source = package(app)
    initial = article_edit_state(app, source, True)
    saved = save_manual_article(
        app,
        ManualArticleEdit(
            package_id=source,
            draft_id=initial.draft.id,
            title="人工确认新标题",
            body=initial.draft.submission.body,
        ),
    )
    with app.unit() as work:
        with pytest.raises(ValueError, match="内容已变"):
            require_reviewable_package(work, saved.model_copy(update={"body": "tampered"}))
        with pytest.raises(ValueError, match="准确来源"):
            require_reviewable_package(work, saved.model_copy(update={"edit_parent_id": "missing"}))
        with pytest.raises(ValueError, match="待审内容已变化"):
            approve_package(
                work,
                ApprovePackage(
                    package_id=saved.id,
                    expected_fingerprint=package_fingerprint(initial.package),
                    reason="确认已读当前准确图文版本。",
                ),
            )
    api = deferred_production_api(app)
    try:
        with pytest.raises(ValueError, match="无需再次调用模型"):
            api.post("/api/production/edit-review", {"package_id": saved.id})
        assert not api.executor.calls
    finally:
        api.close()
    with app.unit() as work:
        story = work.stories.get(saved.story_id)
        work.stories.save(
            story.model_copy(
                update={
                    "status": "deferred",
                    "revision": story.revision + 2,
                }
            ),
            story.revision,
        )
        # 历史模型复核要求返修只改变故事状态，不能阻断人工确认同一准确图文。
        require_reviewable_package(work, saved)
    queue_article_edit(
        app,
        ArticleEditRequest(
            package_id=saved.id,
            draft_id=saved.draft_id,
            request_id="busy-approval",
            instruction="改标题",
        ),
    )
    with pytest.raises(ValueError, match="待执行或运行中"):
        approve_current(app, saved.id)


def test_manual_source_removal_preserves_evidence_and_survives_edits_and_restore(production):
    from bs4 import BeautifulSoup

    from agent4pharma.modules.content.article_editing import ManualArticleEdit
    from agent4pharma.modules.publishing.article_layout import LayoutRequest
    from agent4pharma.workflows.article_editing import article_edit_state
    from agent4pharma.workflows.article_product_editing import ProductEditAction, restore_product
    from agent4pharma.workflows.manual_article_editing import save_manual_article

    app, _ = production
    candidates = app.candidates

    def linked_candidates(ids):
        result = candidates(ids)
        return [
            c.model_copy(
                update={
                    "evidence": [
                        c.evidence[0].model_copy(update={"url": "https://example.test/paper"}),
                        c.evidence[0].model_copy(
                            update={
                                "id": "bad-source",
                                "title": "Paper Close banner",
                                "url": "https://example.test/error",
                            }
                        ),
                    ]
                }
            )
            for c in result
        ]

    app.candidates = linked_candidates
    commission(app)
    task = app.execution.claim(pending(app, "writer").id)
    submission = draft()
    submission = submission.model_copy(
        update={
            "claims": submission.claims
            + [
                submission.claims[0].model_copy(update={"evidence_id": "bad-source"}),
            ]
        }
    )
    app.handoff.content(task.id, task.lease_token, submission)
    review(app)
    source = app.create_layout(LayoutRequest(package_id=package(app)))
    initial = article_edit_state(app, source.id, True)
    assert "excluded_source_ids" not in source.presentation.model_dump()
    request = ManualArticleEdit(
        package_id=source.id,
        draft_id=initial.draft.id,
        title=submission.title,
        body=submission.body,
        excluded_source_ids=["bad-source"],
    )
    with pytest.raises(ValueError, match="不属于当前稿件"):
        save_manual_article(app, request.model_copy(update={"excluded_source_ids": ["foreign"]}))
    saved = save_manual_article(app, request)
    assert saved.presentation.excluded_source_ids == ["bad-source"]
    assert save_manual_article(app, request).id == saved.id
    links = BeautifulSoup(saved.body, "html.parser").select("footer a")
    assert [a["href"] for a in links] == ["https://example.test/paper"]
    assert "Close banner" not in saved.presentation.wechat_html
    changed = article_edit_state(app, saved.id, True)
    assert changed.draft.submission == initial.draft.submission
    assert changed.draft.evidence == initial.draft.evidence
    edited = save_manual_article(
        app,
        ManualArticleEdit(
            package_id=saved.id,
            draft_id=saved.draft_id,
            title="Further manual edit",
            body=submission.body,
        ),
    )
    assert "Close banner" not in edited.body
    restored = restore_product(app, ProductEditAction(package_id=edited.id, restore_package_id=source.id))
    assert "Close banner" in restored.body
    assert not restored.presentation.excluded_source_ids


def test_product_edit_rejects_whole_article_and_wrong_caption(production):
    from agent4pharma.modules.content.article_editing import EditOption, TextChange
    from agent4pharma.workflows.article_editing import article_edit_state
    from agent4pharma.workflows.article_product_editing import edit_submission

    app, _ = production
    commission(app)
    submit_content(app)
    review(app)
    source = package(app)
    state = article_edit_state(app, source, True)
    body = state.draft.submission.body
    option = EditOption(
        label="重写",
        explanation="不应被接受",
        changes=[TextChange(start=0, end=len(body), text=body, replacement=body)],
    )
    with pytest.raises(ValueError, match="整篇重写"):
        edit_submission(state.package, state.draft, option)
    option = EditOption(
        label="图注",
        explanation="不存在的图注",
        changes=[TextChange(target="caption:0", start=0, end=1, text="图", replacement="图一")],
    )
    with pytest.raises(ValueError, match="不属于"):
        edit_submission(state.package, state.draft, option)


def test_product_edit_preserves_images_and_reviews_changed_captions(production):
    from agent4pharma.modules.content.article_editing import (
        ArticleEditRequest,
        ArticleEditResult,
        EditOption,
        TextChange,
    )
    from agent4pharma.modules.materials.document_reading import DocumentReference
    from agent4pharma.modules.publishing.article_layout import FigureImage, FigureSelection, LayoutRequest
    from agent4pharma.workflows.article_editing import (
        article_edit_state,
        queue_article_edit,
        submit_article_edit,
    )
    from agent4pharma.workflows.article_product_editing import (
        ProductEditAction,
        restore_product,
    )

    app, _ = production
    story, _ = commission(app)
    submit_content(app)
    review(app)
    source = package(app)
    with app.unit() as work:
        evidence = work.drafts.evidence(story)[0]
        work.drafts.add_evidence(
            story,
            evidence.model_copy(
                update={
                    "id": "image-paper",
                    "document": DocumentReference(id="bound", sha256="a" * 64, pages=3),
                }
            ),
        )
    app.figure_reader = lambda document, selection: FigureImage(b"png-test", 200, 300, "p2b3")
    original = app.create_layout(
        LayoutRequest(
            package_id=source,
            figures=[
                FigureSelection(
                    evidence_id="image-paper",
                    page=2,
                    block=3,
                    after_paragraph=1,
                    label="原图",
                    caption="实验结果",
                )
            ],
        )
    )
    app.figure_reader = lambda *_: pytest.fail("unchanged image must reuse product bytes")
    initial = article_edit_state(app, original.id, True)
    queued = queue_article_edit(
        app,
        ArticleEditRequest(
            package_id=original.id,
            draft_id=initial.draft.id,
            request_id="caption",
            auto_apply=True,
            instruction="完善图注",
            figure_indices=[0],
        ),
    )
    task = app.execution.claim(queued.id)
    identity = submit_article_edit(
        app,
        task.id,
        task.lease_token,
        ArticleEditResult(
            message="已修改图注。",
            options=[
                EditOption(
                    label="图注",
                    explanation="明确实验限定。",
                    changes=[
                        TextChange(
                            target="caption:0",
                            start=0,
                            end=4,
                            text="实验结果",
                            replacement="本实验条件下的结果",
                        )
                    ],
                )
            ],
        ),
    )
    changed = article_edit_state(app, identity, True)
    assert (
        changed.package.presentation.figures[0].image_sha256 == original.presentation.figures[0].image_sha256
    )
    assert changed.package.presentation.figures[0].selection.caption == "本实验条件下的结果"
    assert "本实验条件下的结果" in changed.package.presentation.wechat_html
    assert changed.draft.submission.body == initial.draft.submission.body
    approve_current(app, identity)
    assert "本实验条件下的结果" in app.export_package(identity, "wechat")[0]
    assert not [t for t in app.snapshot().tasks if t.status == "queued"]
    restored = restore_product(app, ProductEditAction(package_id=identity, restore_package_id=original.id))
    assert restored.presentation.figures[0].selection.caption == "实验结果"
    changed = article_edit_state(app, restored.id, True)
    identity = restored.id
    # 人工定稿和撤销后仍能继续定点修改，不重新进入作者整篇写作。
    assert (
        queue_article_edit(
            app,
            ArticleEditRequest(
                package_id=identity,
                draft_id=changed.draft.id,
                request_id="after-review",
                auto_apply=True,
                instruction="修正图注限定",
            ),
        ).status
        == "queued"
    )


def test_product_edit_failed_request_is_idempotent_and_explicit_retry_keeps_product(production):
    from agent4pharma.modules.content.article_editing import ArticleEditRequest
    from agent4pharma.workflows.article_editing import article_edit_state, queue_article_edit

    app, _ = production
    commission(app)
    submit_content(app)
    review(app)
    source = package(app)
    state = article_edit_state(app, source, True)
    request = ArticleEditRequest(
        package_id=source,
        draft_id=state.draft.id,
        request_id="failed-product",
        auto_apply=True,
        instruction="修改标题",
    )
    task = app.execution.claim(queue_article_edit(app, request).id)
    app.execution.fail(task, "offline controlled failure")
    api = deferred_production_api(app)
    try:
        response = api.post("/api/production/edit-generate", request.model_dump())
        assert response["status"] == "failed" and not api.executor.calls
        failed = app.execution.get(task.id)
        retry = control_task(app, failed, "retry")
        assert retry.status == "queued" and retry.phase == "article-revision"
        assert ProductionContext.model_validate_json(retry.input_json).article_edit == request
    finally:
        api.close()


@pytest.mark.parametrize("header_status", ["ready", "skipped", "failed"])
def test_automatic_pdf_header_submission_edit_and_export(production, header_status):
    import base64
    import hashlib

    from agent4pharma.modules.materials.document_reading import DocumentReference
    from agent4pharma.modules.publishing.paper_header import PaperHeader
    from agent4pharma.workflows.article_product_editing import render_edited_product

    app, _ = production
    original_candidates = app.candidates

    def candidates(ids):
        return [
            c.model_copy(
                update={
                    "evidence": [
                        e.model_copy(
                            update={"document": DocumentReference(id="bound", sha256="a" * 64, pages=1)}
                        )
                        for e in c.evidence
                    ]
                }
            )
            for c in original_candidates(ids)
        ]

    app.candidates = candidates
    png = b"offline-header-fixture"
    header = PaperHeader(
        status=header_status,
        reason="test optional result",
        width=800,
        height=200,
        image_sha256=hashlib.sha256(png).hexdigest(),
        png_base64=base64.b64encode(png).decode(),
        document_sha256="a" * 64,
        crop=[0, 0, 400, 100],
    )
    calls = []

    def read_header(document, title):
        calls.append((document, title))
        return header

    app.header_reader = read_header
    commission(app)
    submit_content(app)
    review(app)
    task = app.execution.claim(pending(app, "publisher").id)
    identity = app.submit_package(
        task.id,
        task.lease_token,
        ChannelSubmission(channel="local_html", release_note="自动题头离线集成验证，不对外发布。"),
    )
    with app.unit() as work:
        saved = next(p for p in work.releases.packages() if p.id == identity)
        current_draft = work.drafts.get(saved.draft_id)
    assert len(calls) == 1 and calls[0][0].sha256 == "a" * 64
    assert saved.presentation.paper_header.status == header_status
    assert ("data-paper-header" in saved.body) == (header_status == "ready")
    app.header_reader = lambda *_: pytest.fail("editing must preserve saved header")
    body, presentation = render_edited_product(app, saved, current_draft, [])
    assert presentation.paper_header == saved.presentation.paper_header
    assert ("data-paper-header" in body) == (header_status == "ready")
    approve_current(app, identity)
    assert ("data-paper-header" in app.export_package(identity, "wechat")[0]) == (header_status == "ready")


def test_news_outline_gate_preserves_legacy_and_versions(production):
    app, _ = production
    writer, tools = commission_writer(app)
    packet = writing_packet()
    without = packet.model_copy(update={"outline": None})
    first = tools.save_writing_research(without)
    with pytest.raises(ValueError, match="新闻骨架"):
        tools.submit_draft(draft())
    assert app.execution.get(writer.id).status == "running"
    with app.unit() as work:
        assert work.drafts.list() == []
    second = tools.save_writing_research(packet, expected_version=first.version)
    assert tools.save_writing_research(packet, expected_version=first.version) == second
    assert app.context(tools.task_id, tools.token).writing_research.research.outline == packet.outline
    tools.submit_draft(draft())
    editor = app.execution.claim(pending(app, "reviewer").id)
    assert app.context(editor.id, editor.lease_token).writing_research == second


def test_legacy_writer_without_outline_keeps_frozen_contract(production):
    from agent4pharma.workflows.writing_research import require_writing_research

    app, _ = production
    writer, tools = commission_writer(app)
    tools.save_writing_research(writing_packet().model_copy(update={"outline": None}))
    saved = app.execution.get(writer.id)
    legacy_bundle = saved.newsroom_skills.model_copy(update={"outline_policy": ""})
    legacy_task = saved.model_copy(update={"newsroom_skills": legacy_bundle})
    assert require_writing_research(legacy_task).research.outline is None
    assert "outline_policy" not in legacy_bundle.model_dump()
    assert "outline" not in writing_packet().model_copy(update={"outline": None}).model_dump()


def test_outline_rejects_unbound_and_fabricated_locations(production):
    app, _ = production
    writer, tools = commission_writer(app)
    packet = writing_packet()
    for update, error in [({"evidence_id": "foreign"}, "未绑定"), ({"location": "PDF页99"}, "位置")]:
        data = packet.model_dump()
        data["outline"]["paragraphs"][0]["sources"][0].update(update)
        with pytest.raises(ValueError, match=error):
            tools.save_writing_research(type(packet).model_validate(data))
    assert not app.execution.get(writer.id).writing_research_json


def test_outline_rejects_missing_or_forward_dependencies():
    from pydantic import ValidationError

    from agent4pharma.modules.content.news_outline import NewsOutline

    outline = writing_packet().outline.model_dump()
    outline["paragraphs"][0]["sources"] = []
    with pytest.raises(ValidationError, match="须引用材料"):
        NewsOutline.model_validate(outline)
    outline["paragraphs"][0]["depends_on"] = [1]
    with pytest.raises(ValidationError, match="前文段号"):
        NewsOutline.model_validate(outline)


def test_outline_sources_include_external_and_exact_windows():
    from agent4pharma.modules.content.news_outline import NewsOutline, validate_outline_sources

    paper = ResearchEvidence(
        id="paper-v1",
        title="Paper",
        text="Observed activity was 10%.",
        origin="primary_fulltext_excerpt",
        read_scope="PDF页3-3",
    )
    web = ResearchEvidence(
        id="web",
        title="External context",
        text="Background result was reported.",
        origin="web_background_page",
        url="https://example.test/context",
    )
    data = writing_packet().outline.model_dump()
    source = data["paragraphs"][0]["sources"][0]
    source["location"] = paper.read_scope
    data["paragraphs"][0]["sources"].append(
        {
            "evidence_id": web.id,
            "source_span": {"content_hash": web.content_hash, "start": 0, "end": len(web.text)},
            "location": web.url,
            "use": "Explain the background, not the primary result.",
        }
    )
    data["paragraphs"].append(
        {
            "question": "What follows from this result?",
            "message": "Summarize the supported change.",
            "position_reason": "Close after the evidence has answered the question.",
            "depends_on": [1],
        }
    )
    validate_outline_sources(NewsOutline.model_validate(data), [paper, web])
    source["source_span"]["end"] = len(paper.text) + 1
    with pytest.raises(ValueError, match="范围"):
        validate_outline_sources(NewsOutline.model_validate(data), [paper, web])
    source["source_span"]["end"] = len(paper.text)
    source["source_span"]["content_hash"] = "0" * 64
    with pytest.raises(ValueError, match="版本"):
        validate_outline_sources(NewsOutline.model_validate(data), [paper, web])
