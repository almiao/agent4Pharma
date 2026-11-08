"""同一主编/作者任务内版本化保存背景调研；不新建阶段、等待确认或调用模型。"""

from pydantic import TypeAdapter

from agent4pharma.modules.content.background_research import (
    BackgroundResearch,
    BackgroundResearchSnapshot,
    validate_background_research,
)
from agent4pharma.modules.content.draft_contracts import EvidenceExcerpt, ResearchEvidence
from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.shared.clock import utcnow
from agent4pharma.workflows.production_dependencies import ProductionContext, ProductionUnit
from agent4pharma.workflows.production_evidence import bound_task_evidence, require_skill_reads, task_evidence
from agent4pharma.workflows.production_tasks import require_lease

BACKGROUND_HISTORY = TypeAdapter(list[BackgroundResearchSnapshot])


def background_research_versions(task: ProductionTask) -> list[BackgroundResearchSnapshot]:
    """只读取真实保存的版本，历史无文档时不从搜索记录猜造。"""
    return BACKGROUND_HISTORY.validate_json(task.background_research_json or "[]")


def background_research_reports(task: ProductionTask) -> list[BackgroundResearchSnapshot]:
    """保留上游真实调研及当前阶段最新版本，供阅读与下游复用。"""
    inherited = ProductionContext.model_validate_json(task.input_json).background_research
    versions = background_research_versions(task)
    return [*inherited, versions[-1]] if versions else inherited


def background_research_excerpts(
    task: ProductionTask, evidence: list[ResearchEvidence]
) -> list[EvidenceExcerpt]:
    """按背景报告准确位置提取原文，覆盖当前及历史版本，避免全量倾倒来源。"""
    sources = {source.id: source for source in evidence}
    excerpts = {}
    for snapshot in [*background_research_reports(task), *background_research_versions(task)]:
        for question in snapshot.research.questions:
            for finding in question.findings:
                span = finding.source_span
                source = sources.get(finding.evidence_id)
                if source and source.content_hash == span.content_hash:
                    key = (source.id, span.content_hash, span.start, span.end)
                    excerpts[key] = EvidenceExcerpt(
                        evidence_id=source.id,
                        content_hash=span.content_hash,
                        start=span.start,
                        end=span.end,
                        text=source.text[span.start : span.end],
                    )
    return list(excerpts.values())


def save_background_research(
    work: ProductionUnit,
    task_id: str,
    token: str,
    result: BackgroundResearch,
    expected_version: int,
) -> BackgroundResearchSnapshot:
    """按租约追加版本，同内容重试幂等；调用者持有短事务，不冻结或改写初始输入。"""
    task = work.tasks.get(task_id)
    require_lease(task, token, task.role)
    if task.research_policy != "pitch-first-v1" or (task.role, task.phase) not in {
        ("editor", "topic-selection"),
        ("writer", "draft-writing"),
    }:
        raise ValueError("当前阶段未开放背景调研保存")
    require_skill_reads(task)
    if task.story_id:
        story = work.stories.get(task.story_id)
        if story.status != "active" or story.revision != task.expected_revision:
            raise ValueError("任务书已变化，不能保存过期背景调研")
        evidence = bound_task_evidence(work, task)
    else:
        story = None
        evidence = task_evidence(task)
    validate_background_research(result, evidence)
    history = background_research_versions(task)
    current = history[-1] if history else None
    if current and current.research == result:
        return current
    version = current.version if current else 0
    if expected_version != version:
        raise ValueError(
            f"背景调研版本已变化：本任务当前expected_version={version}；上游报告版本不计入，请读取当前结果后保存"
        )
    snapshot = BackgroundResearchSnapshot(
        task_id=task.id,
        phase=task.phase,
        story_id=task.story_id,
        brief_version=story.brief_version if story else 0,
        version=version + 1,
        created_at=utcnow(),
        research=result,
    )
    work.tasks.save(
        task.model_copy(
            update={
                "background_research_json": BACKGROUND_HISTORY.dump_json([*history, snapshot]).decode(),
                "updated_at": snapshot.created_at,
            }
        )
    )
    return snapshot
