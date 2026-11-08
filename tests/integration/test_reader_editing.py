"""阅读评审的真实SQLite交接测试；离线，不代表模型语义质量。"""

import hashlib
from functools import partial

import pytest

from agent4pharma.composition import open_application_storage
from agent4pharma.infrastructure.persistence.sqlite.production_unit import production_unit
from agent4pharma.modules.content.draft_contracts import (
    ClaimEvidence,
    DraftRevision,
    DraftSubmission,
    ReaderRevisionResponse,
    ResearchEvidence,
)
from agent4pharma.modules.editorial.commission_contracts import CommissionBrief, Story
from agent4pharma.modules.review.reader_review_contracts import (
    ReaderIssue,
    ReaderObservation,
    ReaderReviewSubmission,
)
from agent4pharma.modules.review.review_contracts import ReviewIssue, ReviewSubmission
from agent4pharma.runtime.production_execution import ProductionLimits
from agent4pharma.shared.clock import utcnow
from agent4pharma.workflows.newsroom_skills import load_newsroom_skills
from agent4pharma.workflows.production_agent_tools import ProductionAgentTools
from agent4pharma.workflows.production_dependencies import ProductionContext
from agent4pharma.workflows.production_queue import ProductionRoleDefinition, queue_role
from agent4pharma.workflows.production_tasks import ControlProductionTask
from agent4pharma.workflows.production_workbench import ProductionWorkbench


@pytest.fixture
def reader_app(tmp_path):
    open_application_storage(tmp_path).close()
    definitions = {
        r: ProductionRoleDefinition(
            role=r,
            prompt="offline",
            model="offline",
            endpoint="https://example.test",
            newsroom_skills=load_newsroom_skills(r),
        )
        for r in ("editor", "writer", "reviewer", "publisher")
    }
    app = ProductionWorkbench(
        partial(production_unit, tmp_path), lambda _: [], definitions, lambda _: [], lambda _: None
    )
    source = ResearchEvidence(
        id="paper", title="test", text="Measured activity was 10%.", origin="test", fulltext=True
    )
    brief = CommissionBrief(
        title="实验变化",
        angle="说明实际结果",
    )
    story = Story(
        id="story", brief=brief, candidate_ids=["candidate"], created_at=utcnow(), updated_at=utcnow()
    )
    submission = DraftSubmission(
        title="实验结果",
        body="Measured activity was 10%. " + "条件及结果已经明确说明。" * 10,
        summary="测试结果",
        claims=[
            ClaimEvidence(
                claim="Measured activity was 10%.", evidence_id="paper", quote="Measured activity was 10%."
            )
        ],
        coverage="准确回答了实验变化与适用条件。",
    )
    draft = DraftRevision(
        id="draft",
        story_id=story.id,
        brief_version=1,
        number=1,
        author_id="writer",
        submission=submission,
        evidence=[source],
        content_hash=hashlib.sha256(submission.model_dump_json().encode()).hexdigest(),
        created_at=utcnow(),
    )
    with app.unit() as work:
        work.stories.save(story, 0)
        work.drafts.save(draft)
        work.drafts.add_evidence(story.id, source)
        task = queue_role(
            work,
            definitions,
            "reviewer",
            ProductionContext(
                story=story,
                draft=draft,
                evidence=[source],
                phase="reading-review",
                reader_policy="reader-quality-v1",
            ),
            ProductionLimits(),
        )
        work.tasks.save(task.model_copy(update={"auto_run": True}))
    return app, task


def tools_for(app, queued):
    task = app.execution.claim(queued.id)
    tools = ProductionAgentTools(app, task.id, task.lease_token)
    for name in task.required_resources:
        tools.read_newsroom_resource(name)
    return task, tools


def observation(tools):
    result = tools.save_reader_observation(
        ReaderObservation(
            news_understood="读到了实验条件下的实际结果。",
            reason_to_read="了解这次实验的具体变化。",
            first_obstacle="没有明显障碍",
        )
    )
    tools.get_reader_brief()
    return result


def ready(**kwargs):
    return ReaderReviewSubmission(
        decision="ready",
        summary="已完整检查两项阅读品质，没有必改问题。",
        appeal_and_flow="completed",
        language_naturalness="completed",
        **kwargs,
    )


def required_issue():
    return ReaderIssue(
        id="explain-result",
        dimension="appeal_and_flow",
        severity="required",
        anchors=["Measured activity was 10%."],
        problem="结果指标尚未解释它对应的测量任务。",
        reader_impact="跨专业读者无法理解这个结果意味着什么。",
        resolution="先解释测量对象，再说明数值与条件。",
    )


def test_blind_view_observation_gate_and_phase_permissions(reader_app):
    app, queued = reader_app
    task, tools = tools_for(app, queued)
    view = tools.get_reader_draft().model_dump()
    assert set(view) == {"draft_id", "draft_hash", "title", "body"}
    with pytest.raises(ValueError, match="先保存"):
        tools.get_reader_brief()
    with pytest.raises(ValueError, match="分步读取"):
        tools.get_production_context()
    with pytest.raises(ValueError, match="独立阅读"):
        tools.submit_review(ReviewSubmission(decision="pass", summary="不应允许此阶段提交事实审核。"))
    observation(tools)
    saved_at = app.execution.get(task.id).reader_observation_created_at
    assert saved_at
    assert tools.get_reader_brief().brief.title == "实验变化"
    observation(tools)
    assert app.execution.get(task.id).reader_observation_created_at == saved_at
    with pytest.raises(ValueError, match="不能覆盖"):
        tools.save_reader_observation(
            ReaderObservation(
                news_understood="改变观察内容不应该被允许。",
                reason_to_read="另一条阅读理由",
                first_obstacle="没有",
            )
        )
    assert app.execution.get(task.id).status == "running"


def test_reader_ready_persists_and_facts_then_publishing(reader_app):
    app, queued = reader_app
    task, tools = tools_for(app, queued)
    observation(tools)
    identity = tools.submit_reader_review(ready())
    with app.unit() as work:
        report = work.reviews.reader_list()[0]
        assert report.id == identity and not work.reviews.list()
        assert report.draft_id == "draft" and report.audience_hash and report.rubric_hash
    next_task = app.next_task(app.execution.get(task.id))
    assert next_task.phase == "fact-check" and next_task.auto_run
    fact, fact_tools = tools_for(app, next_task)
    fact_tools.submit_review(
        ReviewSubmission(decision="pass", summary="事实与任务书已核对，未发现必改问题。")
    )
    package = app.next_task(app.execution.get(fact.id))
    assert package.phase == "layout-production"
    assert ProductionContext.model_validate_json(package.input_json).reader_reviews[-1].id == identity


def test_invalid_anchors_incomplete_and_missing_observation_reject(reader_app):
    app, queued = reader_app
    _, tools = tools_for(app, queued)
    with pytest.raises(ValueError, match="首次阅读"):
        tools.submit_reader_review(ready())
    observation(tools)
    with pytest.raises(ValueError, match="未完成"):
        tools.submit_reader_review(ready().model_copy(update={"language_naturalness": "incomplete"}))
    suggestion = required_issue().model_copy(update={"severity": "suggestion"})
    with pytest.raises(ValueError, match="只收录必须返修"):
        tools.submit_reader_review(ready().model_copy(update={"issues": [suggestion]}))
    bad = required_issue().model_copy(update={"anchors": ["文章里没有这句话"]})
    with pytest.raises(ValueError, match="准确存在"):
        tools.submit_reader_review(
            ReaderReviewSubmission(
                decision="changes_requested",
                summary="存在需要处理的阅读障碍。",
                appeal_and_flow="completed",
                language_naturalness="completed",
                issues=[bad],
            )
        )
    with app.unit() as work:
        assert not work.reviews.reader_list()


def test_one_reading_revision_then_manual_and_no_fact_bypass(reader_app):
    app, queued = reader_app
    task, tools = tools_for(app, queued)
    observation(tools)
    result = ReaderReviewSubmission(
        decision="changes_requested",
        summary="存在具体阅读障碍，需要作者修改。",
        appeal_and_flow="completed",
        language_naturalness="completed",
        issues=[required_issue()],
    )
    tools.submit_reader_review(result)
    writer = app.next_task(app.execution.get(task.id))
    assert writer.role == "writer"
    writer, writer_tools = tools_for(app, writer)
    context = ProductionContext.model_validate_json(writer.input_json)
    with pytest.raises(ValueError, match="逐项回应"):
        writer_tools.submit_draft(context.draft.submission)
    writer_tools.submit_draft(
        context.draft.submission.model_copy(
            update={
                "resolved_issue_ids": ["explain-result"],
                "reader_responses": [
                    ReaderRevisionResponse(
                        issue_id="explain-result", action="revised", reason="已在正文补充说明。"
                    )
                ],
            }
        )
    )
    second = app.next_task(app.execution.get(writer.id))
    assert second.phase == "reading-review"
    second, second_tools = tools_for(app, second)
    observation(second_tools)
    with pytest.raises(ValueError, match="前轮全部"):
        second_tools.submit_reader_review(ready())
    second_tools.submit_reader_review(result)
    assert app.next_task(app.execution.get(second.id)) is None
    with app.unit() as work:
        assert work.stories.get("story").status == "deferred"
        assert len(work.reviews.reader_list()) == 2 and not work.reviews.list()
    reconsidered = app.resume("story", "reviewer", ProductionLimits())
    assert reconsidered.phase == "reading-review"
    assert ProductionContext.model_validate_json(reconsidered.input_json).review_mode == "reconsideration"


def test_reading_approval_stale_for_new_draft(reader_app):
    app, queued = reader_app
    task, tools = tools_for(app, queued)
    observation(tools)
    tools.submit_reader_review(ready())
    fact = app.next_task(app.execution.get(task.id))
    with app.unit() as work:
        context = ProductionContext.model_validate_json(fact.input_json)
        context.draft = context.draft.model_copy(update={"id": "different-draft"})
        work.tasks.save(fact.model_copy(update={"input_json": context.model_dump_json()}))
    _, fact_tools = tools_for(app, fact)
    with pytest.raises(ValueError, match="失效"):
        fact_tools.submit_review(ReviewSubmission(decision="pass", summary="不允许把旧阅读结论用于新稿。"))


def test_reading_review_rejects_third_verdict_and_returns_required_to_author(reader_app):
    app, queued = reader_app
    task, tools = tools_for(app, queued)
    observation(tools)
    with pytest.raises(ValueError, match="只能通过或要求返修"):
        tools.submit_reader_review(
            ready().model_copy(
                update={
                    "decision": "rebrief_needed",
                    "summary": "当前报道角度需要主编重新判断，不能靠润色解决。",
                }
            )
        )
    tools.submit_reader_review(
        ready(issues=[required_issue()]).model_copy(update={"decision": "changes_requested"})
    )
    assert app.next_task(app.execution.get(task.id)).phase == "draft-writing"
    with app.unit() as work:
        assert work.stories.get("story").status == "active"
        assert work.reviews.reader_list()[0].submission.decision == "changes_requested"
        assert not work.reviews.list()


def test_severe_fact_issue_requests_author_revision_instead_of_third_verdict(reader_app):
    app, queued = reader_app
    task, tools = tools_for(app, queued)
    observation(tools)
    tools.submit_reader_review(ready())
    fact = app.next_task(app.execution.get(task.id))
    claimed, fact_tools = tools_for(app, fact)
    issue = ReviewIssue(
        id="F-01",
        severity="blocking",
        category="requirement_gap",
        requirement="说明实际结果",
        location="正文结果的解释",
        reason="正文没有说明任务书要求的具体变化和比较对象。",
        resolution="依据已绑定资料补清具体变化，无法支持的说法应收窄或省略。",
    )
    rejected = ReviewSubmission(
        decision="blocked", summary="发现核心要求缺失，需要作者修改。", issues=[issue]
    )
    with pytest.raises(ValueError, match="不一致"):
        fact_tools.submit_review(rejected)
    fact_tools.submit_review(rejected.model_copy(update={"decision": "changes_requested"}))
    assert app.next_task(app.execution.get(claimed.id)).phase == "draft-writing"
    with app.unit() as work:
        assert work.stories.get("story").status == "active"
        assert work.reviews.list()[0].submission.decision == "changes_requested"


def test_old_parent_does_not_acquire_new_reading_policy(reader_app):
    app, queued = reader_app
    with app.unit() as work:
        context = ProductionContext.model_validate_json(queued.input_json)
        context.reader_policy = ""
        old = queued.model_copy(
            update={
                "id": "old-frozen-review",
                "input_json": context.model_dump_json(),
                "phase": "fact-check",
            }
        )
        work.tasks.save(old)
        child = queue_role(
            work,
            app.definitions,
            "writer",
            context.model_copy(update={"phase": "draft-writing", "parent_task_id": old.id}),
            ProductionLimits(),
        )
        assert not ProductionContext.model_validate_json(child.input_json).reader_policy


def test_reader_migration_keeps_historical_payload_and_is_repeatable(tmp_path):
    import sqlite3

    # Build a real previous schema through the historical scripts, without current migration.
    from agent4pharma.infrastructure.persistence.sqlite.migrations import (
        MIGRATION_2,
        MIGRATION_3,
        MIGRATION_4,
        MIGRATION_5,
        MIGRATION_6,
        MIGRATION_7,
        MIGRATION_8,
        MIGRATION_9,
        MIGRATION_10,
        MIGRATION_11,
        migrate,
    )
    from agent4pharma.infrastructure.persistence.sqlite.migrations.v001 import SCHEMA

    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO schema_version(version) VALUES (1)")
        for script in (
            MIGRATION_2,
            MIGRATION_3,
            MIGRATION_4,
            MIGRATION_5,
            MIGRATION_6,
            MIGRATION_7,
            MIGRATION_8,
            MIGRATION_9,
            MIGRATION_10,
            MIGRATION_11,
        ):
            db.executescript(script)
        payload = '{"historical":"unchanged"}'
        db.execute(
            "INSERT INTO editorial_reviews(id,story_id,draft_id,payload) VALUES (?,?,?,?)",
            ("r", "s", "d", payload),
        )
        db.commit()
        migrate(db)
        migrate(db)
        assert db.execute("SELECT payload FROM editorial_reviews").fetchone()[0] == payload
        assert db.execute("SELECT count(*) FROM reader_reviews").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM schema_version WHERE version=12").fetchone()[0] == 1
    backups = list((tmp_path / "backups").glob("*pre-v12*"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 11
        assert db.execute("SELECT payload FROM editorial_reviews").fetchone()[0] == payload


@pytest.mark.parametrize("bundled", [False, True])
def test_reader_mcp_has_only_reading_tools(reader_app, tmp_path, bundled):
    import asyncio
    import sys

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    app, queued = reader_app
    context = ProductionContext.model_validate_json(queued.input_json)
    if bundled:
        submission = context.draft.submission.model_copy(update={"body": "完整正文内容。" * 8571 + "结束。"})
        context.draft = context.draft.model_copy(
            update={
                "submission": submission,
                "content_hash": hashlib.sha256(submission.model_dump_json().encode()).hexdigest(),
            }
        )
        definition = app.definitions["reviewer"].model_copy(update={"newsroom_pipeline": True})
        with app.unit() as work:
            work.tasks.save(queued.model_copy(update={"status": "cancelled"}))
            queued = queue_role(work, {"reviewer": definition}, "reviewer", context, queued.limits)
        assert "get_reader_brief" not in queued.prompt
    task = app.execution.claim(queued.id)

    async def check():
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "agent4pharma",
                "production-mcp",
                "--root",
                str(tmp_path),
                "--task",
                task.id,
                "--lease",
                task.lease_token,
            ],
        )
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                common = {
                    "submit_reader_review",
                    "report_production_blocked",
                    "list_newsroom_skills",
                    "read_newsroom_resource",
                }
                if not bundled:
                    assert names == common | {
                        "get_reader_draft",
                        "save_reader_observation",
                        "get_reader_brief",
                    }
                    before = await session.call_tool("get_reader_brief", {})
                    assert before.is_error
                    draft = await session.call_tool("get_reader_draft", {})
                    assert not draft.is_error
                    assert "claims" not in str(draft.structured_content)
                    return

                assert names == common | {
                    "get_reader_review_context",
                    "record_reader_observation",
                    "read_reader_review",
                }
                args = {
                    "result": ReaderObservation(
                        news_understood="读到了实验条件下的实际结果。",
                        reason_to_read="了解这次实验的具体变化。",
                        first_obstacle="没有明显障碍",
                    ).model_dump()
                }
                early = await session.call_tool("record_reader_observation", args)
                assert early.is_error and not app.execution.get(task.id).reader_observation_json
                draft = await session.call_tool("get_reader_review_context", {})
                assert not draft.is_error
                view = draft.structured_content
                assert set(view) == {
                    "draft_id",
                    "draft_hash",
                    "title",
                    "body",
                    "resources",
                    "saved_observation",
                    "context_receipt",
                }
                assert view["body"] == context.draft.submission.body
                assert view["draft_hash"] == context.draft.content_hash
                assert view["saved_observation"] is None
                assert view["resources"] == [
                    task.newsroom_skills.read(name).model_dump() for name in task.required_resources
                ]
                assert (await session.call_tool("record_reader_observation", {})).is_error
                brief = await session.call_tool("record_reader_observation", args)
                assert not brief.is_error and "context_receipt" in brief.structured_content
                assert brief.structured_content["brief"]["title"] == "实验变化"
                stored = app.execution.get(task.id)
                assert stored.reader_observation_created_at and stored.reader_brief_read
                recovered = await session.call_tool("get_reader_review_context", {})
                assert recovered.structured_content["saved_observation"] == args["result"]
                assert recovered.structured_content["body"] == view["body"]
                assert recovered.structured_content["resources"] == view["resources"]
                assert not (await session.call_tool("record_reader_observation", {})).is_error
                assert not (await session.call_tool("record_reader_observation", args)).is_error
                args["result"]["first_obstacle"] = "不能覆盖首次观察。"
                assert (await session.call_tool("record_reader_observation", args)).is_error
                assert app.execution.get(task.id).reader_observation_json == stored.reader_observation_json
                submitted = await session.call_tool("submit_reader_review", {"result": ready().model_dump()})
                assert not submitted.is_error

    asyncio.run(check())
    if bundled:
        next_task = app.next_task(app.execution.get(task.id))
        assert next_task.phase == "fact-check"


def test_bundled_reader_atomic_handoff_and_frozen_retry(reader_app, monkeypatch):
    import agent4pharma.workflows.reader_editing as reader_editing

    app, queued = reader_app
    definition = app.definitions["reviewer"].model_copy(update={"newsroom_pipeline": True})
    context = ProductionContext.model_validate_json(queued.input_json)
    with app.unit() as work:
        work.tasks.save(queued.model_copy(update={"status": "cancelled"}))
        queued = queue_role(work, {"reviewer": definition}, "reviewer", context, queued.limits)
    task = app.execution.claim(queued.id)
    tools = ProductionAgentTools(app, task.id, task.lease_token)
    view = tools.get_reader_review_context()
    result = ReaderObservation(
        news_understood="读到了实验条件下的实际结果。",
        reason_to_read="了解这次实验的具体变化。",
        first_obstacle="没有明显障碍",
    )

    original_brief = reader_editing.reader_brief

    def fail_brief(*args):
        original_brief(*args)
        raise ValueError("模拟任务书交付失败")

    with monkeypatch.context() as patch:
        patch.setattr(reader_editing, "reader_brief", fail_brief)
        with pytest.raises(ValueError, match="交付失败"):
            tools.record_reader_observation(result)
    saved = app.execution.get(task.id)
    assert not saved.reader_observation_json and not saved.reader_brief_read
    tools.record_reader_observation(result)
    app.execution.fail(task, "模拟传输失败")
    failed = app.execution.get(task.id)
    retry = app.execution.control(
        ControlProductionTask(
            task_id=failed.id, action="retry", expected_status=failed.status, expected_attempt=failed.attempt
        )
    )
    assert retry.context_tools == task.context_tools and retry.prompt == task.prompt
    assert retry.newsroom_skills == task.newsroom_skills
    retried = app.execution.claim(retry.id)
    recovered = ProductionAgentTools(app, retried.id, retried.lease_token)
    assert not app.execution.get(retry.id).skill_reads
    assert recovered.get_reader_review_context().resources == view.resources
    assert recovered.get_reader_review_context().saved_observation is None
    with pytest.raises(ValueError, match="先保存"):
        recovered.record_reader_observation()
    assert app.execution.get(task.id).reader_observation_json == result.model_dump_json()


def submit_current_revision(app, task, tools, ids):
    context = ProductionContext.model_validate_json(task.input_json)
    reading = {
        i.id for r in context.reader_reviews[-1:] for i in r.submission.issues if i.severity == "required"
    }
    return tools.submit_draft(
        context.draft.submission.model_copy(
            update={
                "resolved_issue_ids": ids,
                "reader_responses": [
                    ReaderRevisionResponse(
                        issue_id=i, action="revised", reason="离线测试用于验证返修流程计数。"
                    )
                    for i in reading
                ],
            }
        )
    )


@pytest.mark.parametrize("limit", [5, 10])
def test_configured_budget_allows_exactly_requested_revisions(reader_app, limit):
    app, queued = reader_app
    with app.unit() as work:
        work.tasks.save(
            queued.model_copy(
                update={"limits": queued.limits.model_copy(update={"max_revision_rounds": limit})}
            )
        )
    result = ReaderReviewSubmission(
        decision="changes_requested",
        summary="保留同一必改，用于离线验证额度边界。",
        appeal_and_flow="completed",
        language_naturalness="completed",
        issues=[required_issue()],
    )
    for index in range(limit + 1):
        reader, tools = tools_for(app, queued)
        observation(tools)
        tools.submit_reader_review(result)
        successor = app.next_task(app.execution.get(reader.id))
        if index == limit:
            assert successor is None
            break
        assert successor.role == "writer" and successor.limits.max_revision_rounds == limit
        writer, writer_tools = tools_for(app, successor)
        if index == 0:
            # A genuine failed attempt and explicit retry must not consume a second manuscript revision.
            app.execution.fail(writer, "offline simulated transport failure")
            failed = app.execution.get(writer.id)
            retry = app.execution.control(
                ControlProductionTask(
                    task_id=failed.id,
                    action="retry",
                    expected_status=failed.status,
                    expected_attempt=failed.attempt,
                )
            )
            assert retry.limits == failed.limits
            writer, writer_tools = tools_for(app, retry)
        submit_current_revision(app, writer, writer_tools, ["explain-result"])
        queued = app.next_task(app.execution.get(writer.id))
    with app.unit() as work:
        assert work.stories.get("story").status == "deferred"
        assert len(work.drafts.list()) == limit + 1


def test_reader_and_fact_reviews_share_one_total_budget(reader_app):
    app, queued = reader_app
    with app.unit() as work:
        work.tasks.save(
            queued.model_copy(update={"limits": queued.limits.model_copy(update={"max_revision_rounds": 2})})
        )
    first, tools = tools_for(app, queued)
    observation(tools)
    tools.submit_reader_review(
        ReaderReviewSubmission(
            decision="changes_requested",
            summary="阅读问题需要返修一次。",
            appeal_and_flow="completed",
            language_naturalness="completed",
            issues=[required_issue()],
        )
    )
    writer, writer_tools = tools_for(app, app.next_task(app.execution.get(first.id)))
    submit_current_revision(app, writer, writer_tools, ["explain-result"])
    second, tools = tools_for(app, app.next_task(app.execution.get(writer.id)))
    observation(tools)
    tools.submit_reader_review(ready(closed_issue_ids=["explain-result"]))
    factual, tools = tools_for(app, app.next_task(app.execution.get(second.id)))
    context = ProductionContext.model_validate_json(factual.input_json)
    fact_issue = ReviewIssue(
        id="fact-gap",
        severity="required",
        location="正文",
        category="requirement_gap",
        requirement=context.story.brief.angle,
        reason="核心要求在当前稿件仍没有得到实际回答。",
        resolution="按准确问题说明结果及其条件。",
    )
    changes = ReviewSubmission(
        decision="changes_requested", summary="仍需修订明确的任务书要求。", issues=[fact_issue]
    )
    tools.submit_review(changes)
    writer, writer_tools = tools_for(app, app.next_task(app.execution.get(factual.id)))
    submit_current_revision(app, writer, writer_tools, ["fact-gap"])
    third, tools = tools_for(app, app.next_task(app.execution.get(writer.id)))
    observation(tools)
    tools.submit_reader_review(ready())
    final, tools = tools_for(app, app.next_task(app.execution.get(third.id)))
    tools.submit_review(changes)
    assert app.next_task(app.execution.get(final.id)) is None
    with app.unit() as work:
        assert len(work.drafts.list()) == 3
        assert work.stories.get("story").status == "deferred"


@pytest.mark.parametrize("limit", [0, 11])
def test_total_revision_limit_rejects_out_of_range(limit):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ProductionLimits(max_revision_rounds=limit)
