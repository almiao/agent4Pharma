"""节点上下文的真实SQLite边界回归；没有模型调用。"""

import json

import pytest
from test_production import commission_writer, control_task, draft, read_stage_skills, writing_packet
from test_production import production as production_fixture

from agent4pharma.modules.content.draft_contracts import DraftRevision, ResearchEvidence
from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.workflows.production_agent_tools import ProductionAgentTools
from agent4pharma.workflows.production_dependencies import ProductionContext
from agent4pharma.workflows.production_tool_profile import production_tool_names
from agent4pharma.workflows.writing_research import writing_research_cursor

production = production_fixture


def test_source_catalog_cannot_expand_to_story_latest_or_dump_text(production):
    app, _ = production
    writer, tools = commission_writer(app)
    foreign = ResearchEvidence(
        id="late", title="Late", text="unrelated secret body", origin="operator_document"
    )
    with app.unit() as work:
        work.drafts.add_evidence(writer.story_id, foreign)
    assert "late" not in {s.id for s in tools.list_task_sources().items}
    assert "Observed activity" not in tools.list_task_sources().model_dump_json()
    with pytest.raises(ValueError, match="绑定任务"):
        tools.read_evidence("late")
    app.register_search_results(writer.id, writer.lease_token, [foreign])
    assert "late" in {s.id for s in tools.list_task_sources().items}
    assert tools.read_evidence("late").text == foreign.text
    assert (
        foreign not in ProductionContext.model_validate_json(app.execution.get(writer.id).input_json).evidence
    )


def test_source_cursor_rejects_scope_change_and_pages_without_text(production):
    app, _ = production
    writer, tools = commission_writer(app)
    sources = [
        ResearchEvidence(id=f"s{i}", title="Source", text="body", origin="operator_document")
        for i in range(21)
    ]
    app.register_search_results(writer.id, writer.lease_token, sources)
    first = tools.list_task_sources()
    assert len(first.items) == 10 and first.has_more
    second = tools.list_task_sources(first.next_cursor)
    assert len(second.items) == 10
    assert not ({i.id for i in first.items} & {i.id for i in second.items})
    app.register_search_results(writer.id, writer.lease_token, [sources[0].model_copy(update={"id": "new"})])
    with pytest.raises(ValueError, match="CONTEXT_VERSION_MISMATCH"):
        tools.list_task_sources(first.next_cursor)


def test_research_is_single_item_and_old_ref_cannot_read_new_version(production):
    app, _ = production
    writer, tools = commission_writer(app)
    saved = tools.save_writing_research(writing_packet())
    checkpoint = tools.get_task_checkpoint()
    assert checkpoint.writing.write_expected_version == 1
    assert saved.research.answer not in checkpoint.model_dump_json()
    catalog = tools.list_research_items()
    answer = next(i for i in catalog.items if i.section == "answer")
    assert saved.research.answer not in catalog.model_dump_json()
    one = tools.read_research_item(answer.ref)
    assert one.value == saved.research.answer
    assert "outline" not in one.model_dump_json()
    changed = saved.research.model_copy(update={"answer": saved.research.answer + " Additional context."})
    tools.save_writing_research(changed, checkpoint.writing.write_expected_version)
    with pytest.raises(ValueError, match="CONTEXT_VERSION_MISMATCH"):
        tools.read_research_item(answer.ref)
    assert tools.get_task_checkpoint().writing.write_expected_version == 2
    assert app.execution.get(writer.id).status == "running"


def test_retry_preserves_frozen_fields_and_inherited_cursor_is_writable(production):
    app, _ = production
    writer, tools = commission_writer(app)
    saved = tools.save_writing_research(writing_packet())
    original = json.loads(writer.input_json)
    assert "review_mode" not in original
    paused = control_task(app, app.execution.get(writer.id), "pause")
    retry = app.execution.claim(control_task(app, paused, "resume").id)
    restored = json.loads(retry.input_json)
    assert "review_mode" not in restored
    assert retry.prompt == writer.prompt and retry.limits == writer.limits
    assert retry.context_contract == writer.context_contract == "node-context-v2"
    assert restored["writing_research"]["task_id"] == writer.id
    assert not retry.writing_research_json
    current = read_stage_skills(app, retry)
    cursor = current.get_task_checkpoint().writing
    assert cursor.origin_task_id == writer.id and cursor.write_expected_version == 0
    changed = saved.research.model_copy(update={"answer": saved.research.answer + " Clarified."})
    updated = current.save_writing_research(changed, cursor.write_expected_version)
    assert updated.version == 1 and updated.task_id == retry.id
    assert app.execution.get(writer.id).writing_research_json


def test_legacy_cursor_matches_save_semantics_without_auto_upgrading(production):
    app, _ = production
    writer, tools = commission_writer(app)
    saved = tools.save_writing_research(writing_packet())
    context = ProductionContext.model_validate_json(writer.input_json).model_copy(
        update={"writing_research": saved}
    )
    legacy = app.execution.get(writer.id).model_copy(
        update={"context_contract": "", "input_json": context.model_dump_json(), "writing_research_json": ""}
    )
    with app.unit() as work:
        work.tasks.save(legacy)
    assert writing_research_cursor(legacy).write_expected_version == saved.version
    assert "get_production_context" in production_tool_names(legacy)
    changed = saved.research.model_copy(update={"answer": saved.research.answer + " Clarified."})
    assert tools.save_writing_research(changed, saved.version).version == saved.version + 1


@pytest.mark.parametrize("phase", ["reading-review", "article-revision", "layout-production"])
def test_forbidden_research_cannot_be_recovered_through_catalog(production, phase):
    app, _ = production
    writer, tools = commission_writer(app)
    with app.unit() as work:
        work.tasks.save(writer.model_copy(update={"phase": phase}))
    with pytest.raises(ValueError, match="CONTEXT_SCOPE_DENIED"):
        tools.list_research_items()
    if phase == "reading-review":
        with pytest.raises(ValueError):
            tools.list_task_sources()
        with pytest.raises(ValueError):
            tools.read_evidence("paper-v1")


def test_revision_entry_and_source_lookup_never_repeat_whole_research(production):
    app, _ = production
    writer, tools = commission_writer(app)
    saved = tools.save_writing_research(writing_packet())
    context = app.context(writer.id, writer.lease_token)
    original_draft = DraftRevision(
        id="exact-draft",
        story_id=writer.story_id,
        brief_version=1,
        number=1,
        author_id="writer",
        content_hash="a" * 64,
        created_at=writer.created_at,
        submission=draft(),
        evidence=context.evidence,
    )
    context.draft = original_draft
    with app.unit() as work:
        task = work.tasks.get(writer.id)
        work.tasks.save(
            task.model_copy(
                update={
                    "input_json": context.model_dump_json(),
                    "required_resources": ("skills/draft-writing/core/revision.md",),
                    "context_contract": "node-context-v1",
                    "context_tools": (),
                }
            )
        )
    working = tools.get_revision_context()
    assert working.draft.submission.body == original_draft.submission.body
    assert working.sources.items and working.checkpoint.writing.current_version == saved.version
    assert saved.research.answer not in working.model_dump_json()
    names = production_tool_names(app.execution.get(writer.id))
    assert "get_production_context" not in names and "get_stage_context" not in names
    assert {"get_revision_context", "list_task_sources", "read_research_item"} <= set(names)
    from agent4pharma.workflows.production_reading_view import production_reading_view

    legacy_size = len(
        production_reading_view(app.context(writer.id, writer.lease_token), 10).model_dump_json()
    )
    with pytest.raises(ValueError, match="CONTEXT_SCOPE_DENIED"):
        tools.get_production_context(recovery_only=True)
    catalog_size = len(tools.list_task_sources().model_dump_json())
    assert catalog_size < legacy_size / 3


def test_unknown_context_contract_rejected_and_legacy_serialization_omits_stamp(production):
    app, _ = production
    writer, _ = commission_writer(app)
    payload = writer.model_dump(mode="json")
    payload["context_contract"] = "future-unknown"
    with pytest.raises(ValueError):
        ProductionTask.model_validate(payload)
    legacy = app.execution.get(writer.id).model_copy(update={"context_contract": ""})
    assert "context_contract" not in legacy.model_dump(mode="json")
    with pytest.raises(ValueError, match="租约"):
        ProductionAgentTools(app, writer.id, "wrong").list_task_sources()


def test_context_report_endpoints_are_read_only_and_keep_exact_attempt(production):
    from agent4pharma.entrypoints.web.production_api import ProductionAPI
    from agent4pharma.infrastructure.persistence.sqlite.article_regression_repository import (
        ObservationRepository,
    )

    app, root = production
    writer, _ = commission_writer(app)
    before = app.execution.get(writer.id).model_dump_json()
    api = ProductionAPI(
        app, lambda _: pytest.fail("read must not execute"), lambda: False, ObservationRepository(root)
    )
    try:
        summary = api.get(f"/api/production/tasks/{writer.id}/context-summary?attempt=1")
        assert summary["contract"] == "node-context-v2"
        assert summary["diagnostics"][0]["code"] == "CONTEXT_MANIFEST_INCOMPLETE"
        page = api.get(f"/api/production/tasks/{writer.id}/context-manifests?attempt=1")
        assert page["items"] == [] and not page["has_more"]
        with pytest.raises(ValueError):
            api.get(f"/api/production/tasks/{writer.id}/context-summary?attempt=2")
    finally:
        api.close()
    assert app.execution.get(writer.id).model_dump_json() == before


def test_published_legacy_resource_references_are_only_adapted_for_new_contract(production):
    from agent4pharma.workflows.newsroom_skills import load_newsroom_skills

    app, _ = production
    writer, _ = commission_writer(app)
    base = load_newsroom_skills("writer")
    assert any("get_production_context" in r.text for r in base.resources)
    assert not any("get_production_context" in r.text for r in writer.newsroom_skills.resources)
    assert writer.newsroom_skills.content_hash != base.content_hash
    assert writer.context_tools == production_tool_names(writer)
    assert "get_production_context" not in writer.prompt


def test_compact_reuse_receipt_returns_next_local_cursor_not_inherited_version(production):
    app, _ = production
    writer, tools = commission_writer(app)
    saved = tools.save_writing_research(writing_packet())
    paused = control_task(app, app.execution.get(writer.id), "pause")
    retry = app.execution.claim(control_task(app, paused, "resume").id)
    read_stage_skills(app, retry)
    compact = ProductionAgentTools(app, retry.id, retry.lease_token, compact_receipts=True)
    cursor = compact.save_writing_research(saved.research, receipt_only=False)
    assert cursor == 0 and compact.get_task_checkpoint().writing.current_version == 1
    changed = saved.research.model_copy(update={"answer": saved.research.answer + " Clarification."})
    assert compact.save_writing_research(changed, cursor) == 1
    assert compact.get_task_checkpoint().writing.write_expected_version == 1


def test_layout_prompt_does_not_advertise_writer_research_tools(production):
    from test_production import enable_newsroom_pipeline

    from agent4pharma.runtime.production_execution import ProductionLimits
    from agent4pharma.workflows.production_queue import queue_role

    app, _ = production
    enable_newsroom_pipeline(app)
    with app.unit() as work:
        task = queue_role(
            work,
            app.definitions,
            "publisher",
            ProductionContext(phase="layout-production"),
            ProductionLimits(),
        )
    assert "get_stage_context" in task.prompt
    assert "save_writing_research" not in task.prompt
    assert "save_background_research" not in task.prompt
    assert "list_research_items" not in task.prompt
    assert "read_document" not in production_tool_names(task)
