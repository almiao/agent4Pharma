"""RR-04式局部返修的稿件、证据、候选恢复和独立审核闭环；离线业务场景，不冒充模型实验。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_production import (
    commission_writer,
    control_task,
    draft,
    pending,
    writing_packet,
)
from test_production import production as production_fixture

from agent4pharma.infrastructure.persistence.sqlite.production_unit import production_unit
from agent4pharma.modules.content.draft_contracts import (
    ClaimEvidence,
    DraftRevision,
    ReaderRevisionResponse,
    ResearchEvidence,
)
from agent4pharma.modules.content.draft_revision import RevisionChange
from agent4pharma.modules.content.revision_workspace import CandidateEdit, CandidateStep, RevisionWorkspace
from agent4pharma.modules.materials.document_reading import (
    DocumentContent,
    DocumentReadResult,
    DocumentReference,
)
from agent4pharma.modules.review.reader_review_contracts import (
    ReaderObservation,
    ReaderReviewResult,
    ReaderRevisionIssue,
)
from agent4pharma.workflows.production_agent_tools import EvidenceLookup, ProductionAgentTools
from agent4pharma.workflows.production_dependencies import ProductionContext
from agent4pharma.workflows.production_tool_profile import production_tool_names
from agent4pharma.workflows.revision_workspace import RevisionWorkspaceTools

production = production_fixture
OLD = "这次团队又用流式细胞术确认了Ctcf补救中的一个关键结果。"
NEW = "既往研究用流式细胞术观察到，Ctcf与Arid1a双杂合缺失缓解了暗区细胞增多。"
CURRENT = "本研究进一步采用单细胞多组学分析这一补救。"
SOURCE = (
    "Notably, for the perturbation target Ctcf, recently published work has established a mechanistic "
    "basis for the predicted antagonism between Ctcf and Arid1a and demonstrated a phenotypic rescue "
    "of the Arid1aHet DZ increase in animals with Arid1aHet;CtcfHet double-heterozygous KO "
    "(DKO) using flow cytometry, confirming a key result from our DynaVelo rescue analysis. "
    "To quantitatively validate this rescue, we performed single-cell multiome on GC cells."
)


def prepare_revision(app, *, history=False):
    writer, tools = commission_writer(app)
    tools.save_writing_research(writing_packet())
    source = ResearchEvidence(
        id="ctcf-source",
        title="Ctcf experiment attribution",
        text=SOURCE,
        origin="primary_fulltext_excerpt",
        fulltext=True,
        document=DocumentReference(id="doc", sha256="c" * 64, pages=10),
    )
    app.register_search_results(writer.id, writer.lease_token, [source])
    base = draft()
    base.body += "\n\n" + OLD + "\n\n" + CURRENT
    base.claims.extend(
        [
            ClaimEvidence(
                claim=OLD, evidence_id=source.id, quote=SOURCE[: SOURCE.index("To quantitatively")]
            ),
            ClaimEvidence(
                claim=CURRENT, evidence_id=source.id, quote="we performed single-cell multiome on GC cells"
            ),
        ]
    )
    if history:
        with app.unit() as work:
            work.drafts.save(
                DraftRevision(
                    id="prior-draft",
                    story_id=writer.story_id,
                    brief_version=1,
                    number=1,
                    author_id="prior-writer",
                    content_hash="a" * 64,
                    created_at=writer.created_at,
                    submission=draft(),
                    evidence=[source],
                )
            )
    original_id = tools.submit_draft(base)
    reviewer = app.execution.claim(pending(app, "reviewer").id)
    review = ProductionAgentTools(app, reviewer.id, reviewer.lease_token)
    review.get_reader_review_context()
    review.record_reader_observation(
        ReaderObservation(
            news_understood="需要区分既往流式实验与本研究多组学结果。",
            reason_to_read="理解模型预测如何验证。",
            first_obstacle="流式确认的实验主体归属不清楚。",
        )
    )
    review.submit_reader_review(
        ReaderReviewResult(
            decision="changes_requested",
            summary="澄清Ctcf实验的具体结果与归属。",
            appeal_and_flow="completed",
            language_naturalness="completed",
            issues=[
                ReaderRevisionIssue(
                    id="RR-04",
                    dimension="appeal_and_flow",
                    severity="required",
                    anchors=[OLD],
                    problem="关键结果及实验主体不明确。",
                    reader_impact="读者误以为流式结果是本研究新增实验。",
                    resolution="核对完整原句，区分既往流式与本研究多组学。",
                )
            ],
        )
    )
    task = app.execution.claim(pending(app, "writer").id)
    return task, ProductionAgentTools(app, task.id, task.lease_token), source, original_id


def test_rr04_small_edit_error_feedback_restart_and_independent_review(production):
    app, _ = production
    task, tools, source, original_id = prepare_revision(app)
    workspace = RevisionWorkspaceTools(app, task.id, task.lease_token)
    entry = tools.get_revision_context()
    assert entry.version == 0 and OLD in entry.body and CURRENT in entry.body
    assert entry.issues.required_ids == ["RR-04"]
    assert "claims" not in entry.model_dump() and "sources" not in entry.model_dump()
    assert len(entry.resources) == 2
    assert not task.web_search_enabled
    assert {"submit_draft_revision", "save_writing_research", "save_background_research"}.isdisjoint(
        production_tool_names(task)
    )
    claims = workspace.read_revision_candidate("claims", query="Ctcf")
    assert [row.index for row in claims.claims] == [1]
    invalid = workspace.edit_revision_candidate(
        CandidateEdit(
            changes=[RevisionChange(original="不存在的原文", replacement=NEW)],
            reason="纠正实验归属及具体读出。",
        ),
        0,
    )
    assert invalid.state == "invalid" and invalid.version == 0
    assert not app.execution.get(task.id).revision_workspace_json
    edit = CandidateEdit(
        changes=[RevisionChange(original=OLD, replacement=NEW)],
        added_claims=[ClaimEvidence(claim=NEW, evidence_id=source.id, quote="wrong quote")],
        reason="将流式结果归于既往研究，保留本研究多组学。",
    )
    assert workspace.edit_revision_candidate(edit, 0).version == 1
    feedback = workspace.validate_revision_candidate(1)
    assert feedback.state == "invalid" and feedback.feedback
    assert app.execution.get(task.id).status == "running"
    with app.read_unit() as work:
        assert work.drafts.get(original_id).submission.body.endswith(CURRENT)
        assert OLD in work.drafts.get(original_id).submission.body
        assert len([d for d in work.drafts.list() if d.story_id == task.story_id]) == 1

    paused = control_task(app, app.execution.get(task.id), "pause")
    with pytest.raises(ValueError, match="租约"):
        workspace.restore_revision_candidate(0, 1)
    retry = app.execution.claim(control_task(app, paused, "resume").id)
    workspace = RevisionWorkspaceTools(app, retry.id, retry.lease_token)
    restored = workspace.get_revision_context()
    assert restored.version == 1 and NEW in restored.body and OLD not in restored.body
    assert retry.prompt == task.prompt and retry.context_tools == task.context_tools
    saved = RevisionWorkspace.model_validate_json(retry.revision_workspace_json)
    assert saved.versions[0].origin_task_id == task.id
    with pytest.raises(ValueError, match="版本"):
        workspace.edit_revision_candidate(edit, 0)
    current_claims = workspace.read_revision_candidate("claims", query=NEW)
    replacement_index = current_claims.claims[0].index
    fix = CandidateEdit(
        removed_claim_indices=[replacement_index],
        added_claims=[
            ClaimEvidence(
                claim=NEW,
                evidence_id=source.id,
                quote=SOURCE[: SOURCE.index("To quantitatively")],
            )
        ],
        reason="修正引文为完整原文，保留准确实验归属。",
    )
    assert workspace.edit_revision_candidate(fix, 1).version == 2
    assert workspace.validate_revision_candidate(2).state == "invalid"  # 尚未回应RR-04。
    assert (
        workspace.respond_revision_issue(
            "RR-04", "revised", "核对完整原句后更正归属，保留多组学。", 2
        ).version
        == 3
    )
    assert workspace.validate_revision_candidate(3).state == "ready_for_review"
    diff = workspace.read_revision_candidate("diff").text
    assert OLD in diff and NEW in diff
    # 恢复追加新版本，不抹掉历史或把旧预检沿用于不同版本。
    assert workspace.restore_revision_candidate(1, 3).version == 4
    assert workspace.validate_revision_candidate(4).state == "invalid"
    assert workspace.restore_revision_candidate(3, 4).version == 5
    with pytest.raises(ValueError, match="版本"):
        workspace.submit_revision_candidate(3)
    submitted_id = workspace.submit_revision_candidate(5)
    with app.read_unit() as work:
        old = work.drafts.get(original_id)
        new = work.drafts.get(submitted_id)
        assert new.submission.body == old.submission.body.replace(OLD, NEW)
        assert new.submission.title == old.submission.title
        assert old.submission.claims[0] in new.submission.claims
        assert old.submission.claims[2] in new.submission.claims
        assert new.submission.resolved_issue_ids == ["RR-04"]
    next_task = pending(app, "reviewer")
    assert next_task.phase == "reading-review"
    assert ProductionContext.model_validate_json(next_task.input_json).draft.id == submitted_id
    assert not next_task.revision_workspace_json
    with pytest.raises(ValueError, match="租约"):
        workspace.submit_revision_candidate(5)


def test_cross_tool_content_overlap_and_explicit_reload(production):
    app, _ = production
    task, tools, source, _ = prepare_revision(app)
    first = tools.read_evidence(source.id, 0, 150)
    assert first.text == SOURCE[:150]
    app.document_reader = lambda *_: DocumentReadResult(
        status="available",
        content=DocumentContent(
            sha256=source.document.sha256,
            short_id="doc",
            tier="standard",
            content=SOURCE,
        ),
        locators=["pages:8-10"],
    )
    second = tools.read_document(source.id, 8, 10)
    assert second.text == "" and second.reused[0].start == 0 and second.reused[0].end == 150
    assert second.segments[0].text == SOURCE[150:]
    assert second.segments[0].start == 150
    third = tools.read_evidence(second.evidence_id, 0, len(SOURCE))
    assert third.already_read and not third.text and not third.segments
    explicit = tools.read_evidence(second.evidence_id, 0, len(SOURCE), reread=True)
    assert explicit.text == SOURCE and not explicit.reused
    # 新MCP会话不能继承旧会话的模型记忆假设，即使文档请求缓存还在。
    fresh = ProductionAgentTools(app, task.id, task.lease_token)
    assert fresh.read_document(source.id, 8, 10).text == SOURCE
    queries = [
        EvidenceLookup(evidence_id=source.id, query=term)
        for term in ("flow cytometry", "single-cell multiome")
    ]
    located = fresh.find_bound_evidence_batch(queries)
    assert all(hit.already_read and not hit.text for row in located for hit in row)
    reloaded = fresh.find_bound_evidence_batch(queries, reread=True)
    assert all(hit.text and not hit.reused for row in reloaded for hit in row)


def test_workspace_rejects_changed_issue_scope_and_stale_story(production):
    app, _ = production
    task, tools, _, _ = prepare_revision(app)
    tools.get_revision_context()
    workspace = RevisionWorkspaceTools(app, task.id, task.lease_token)
    workspace.edit_revision_candidate(
        CandidateEdit(title="明确Ctcf流式实验归属", reason="使标题指向具体问题。"), 0
    )
    with app.unit() as work:
        saved = work.tasks.get(task.id)
        context = ProductionContext.model_validate_json(saved.input_json)
        context.reader_reviews = []
        work.tasks.save(saved.model_copy(update={"input_json": context.model_dump_json()}))
    with pytest.raises(ValueError, match="问题集合"):
        workspace.get_revision_context()
    with app.unit() as work:
        story = work.stories.get(task.story_id)
        work.stories.save(
            story.model_copy(update={"status": "cancelled", "revision": story.revision + 1}), story.revision
        )
    with pytest.raises(ValueError, match="选题"):
        workspace.restore_revision_candidate(0, 1)


def test_replay_preparation_copies_history_and_never_executes_model(production, tmp_path):
    app, root = production
    task, _, _, original_id = prepare_revision(app, history=True)
    before = app.execution.get(task.id).model_dump_json()
    target = tmp_path / "isolated"
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "scripts/replay_revision_workspace.py"),
            "--source-root",
            str(root),
            "--source-task",
            task.id,
            "--output-root",
            str(target),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "queued" and report["usage"] is None
    with production_unit(target, read_only=True) as work:
        copies = work.drafts.list()
        assert {d.id for d in copies} == {"prior-draft", original_id}
        assert {d.number for d in copies} == {1, 2}
        assert work.reviews.reader_list()
    assert app.execution.get(task.id).model_dump_json() == before


def test_rr04_combined_step_saves_response_and_validates_without_extra_roundtrip(production):
    app, _ = production
    task, tools, source, original_id = prepare_revision(app)
    tools.get_revision_context()
    workspace = RevisionWorkspaceTools(app, task.id, task.lease_token)
    step = CandidateStep(
        edit=CandidateEdit(
            changes=[RevisionChange(original=OLD, replacement=NEW)],
            added_claims=[
                ClaimEvidence(
                    claim=NEW, evidence_id=source.id, quote=SOURCE[: SOURCE.index("To quantitatively")]
                )
            ],
            reason="纠正流式实验归属并保留本研究多组学结论。",
        ),
        responses=[
            ReaderRevisionResponse(
                issue_id="RR-04",
                action="revised",
                reason="原文完整句明确流式属于既往研究，本研究实施多组学。",
            )
        ],
    )
    receipt = workspace.apply_revision_step(step, 0)
    assert receipt.version == 1 and receipt.state == "ready_for_review"
    saved = RevisionWorkspace.model_validate_json(app.execution.get(task.id).revision_workspace_json)
    assert saved.current(app.context(task.id, task.lease_token).draft).resolved_issue_ids == ["RR-04"]
    with pytest.raises(ValueError, match="版本"):
        workspace.apply_revision_step(step, 0)
    result = workspace.submit_revision_candidate(1)
    assert result != original_id
    assert pending(app, "reviewer").phase == "reading-review"


def test_lifecycle_api_reads_without_model_and_omits_candidate_history(production):
    from agent4pharma.entrypoints.web.production_api import ProductionAPI

    app, _ = production
    task, tools, _, _ = prepare_revision(app)
    calls = []
    api = ProductionAPI(app, calls.append, lambda: False)
    try:
        report = api.get(f"/api/production/tasks/{task.id}/lifecycle-cost")
        assert report["root_task_id"] == task.id
        assert report["requests"] == 0 and report["reported_total_tokens"] is None
        assert report["partial"]
        assert "revision_workspace_json" not in api.get(f"/api/production/tasks/{task.id}")["task"]
        assert calls == []
    finally:
        api.close()


def test_reader_history_is_on_demand_and_preserves_blind_read_gate(production):
    from agent4pharma.modules.content.draft_contracts import ReaderRevisionResponse

    app, _ = production
    task, tools, source, _ = prepare_revision(app)
    tools.get_revision_context()
    workspace = RevisionWorkspaceTools(app, task.id, task.lease_token)
    workspace.apply_revision_step(
        CandidateStep(
            edit=CandidateEdit(
                changes=[RevisionChange(original=OLD, replacement=NEW)],
                added_claims=[
                    ClaimEvidence(
                        claim=NEW, evidence_id=source.id, quote=SOURCE[: SOURCE.index("To quantitatively")]
                    )
                ],
                reason="纠正流式实验归属，保留本研究多组学。",
            ),
            responses=[
                ReaderRevisionResponse(
                    issue_id="RR-04", action="revised", reason="按完整句核对既往实验与本研究的不同归属。"
                )
            ],
        ),
        0,
    )
    workspace.submit_revision_candidate(1)
    review = app.execution.claim(pending(app, "reviewer").id)
    with app.unit() as work:
        context = ProductionContext.model_validate_json(review.input_json)
        older = context.reader_reviews[0].model_copy(update={"id": "older-reader"})
        context.reader_reviews = [older, *context.reader_reviews]
        work.tasks.save(review.model_copy(update={"input_json": context.model_dump_json()}))
    reader_tools = ProductionAgentTools(app, review.id, review.lease_token)
    with pytest.raises(ValueError, match="盲读"):
        reader_tools.read_reader_review(older.id)
    reader_tools.get_reader_review_context()
    brief = reader_tools.record_reader_observation(
        ReaderObservation(
            news_understood="区分既往流式实验与本研究多组学验证。",
            reason_to_read="理解验证结果的边界。",
            first_obstacle="没有理解障碍。",
        )
    )
    assert len(brief.previous_reviews) == 1 and brief.review_history[0].report_id == older.id
    assert reader_tools.read_reader_review(older.id).id == older.id
    with pytest.raises(ValueError, match="冻结任务"):
        reader_tools.read_reader_review("unbound-report")
