"""同一写作任务内保存可恢复研究底稿；不结束租约、不排队后继、不执行模型。"""

from pydantic import BaseModel, TypeAdapter

from agent4pharma.modules.content.draft_contracts import EvidenceExcerpt, ResearchEvidence
from agent4pharma.modules.content.writing_research import (
    WritingResearch,
    WritingResearchSnapshot,
    validate_writing_research,
)
from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.shared.clock import utcnow
from agent4pharma.workflows.production_dependencies import ProductionContext, ProductionUnit
from agent4pharma.workflows.production_evidence import bound_task_evidence, require_skill_reads
from agent4pharma.workflows.production_tasks import require_lease

WRITING_RESEARCH_HISTORY = TypeAdapter(list[WritingResearchSnapshot])


class ResearchReadCursor(BaseModel):
    """区分当前可复用底稿与本任务写游标，读写共用同一版本规则。"""

    origin_task_id: str = ""
    current_version: int = 0
    task_local_version: int = 0
    write_expected_version: int = 0


def writing_research_cursor(task: ProductionTask) -> ResearchReadCursor:
    """旧契约沿用跨任务版本，新契约首次本地修改从零写游标开始。"""
    current = current_writing_research(task)
    history = writing_research_versions(task)
    local = history[-1].version if history else 0
    return ResearchReadCursor(
        origin_task_id=current.task_id if current else "",
        current_version=current.version if current else 0,
        task_local_version=local,
        write_expected_version=local if task.context_contract else (current.version if current else 0),
    )


def writing_research_versions(task: ProductionTask) -> list[WritingResearchSnapshot]:
    """读取本任务实际保存的版本；历史无底稿时保持空，不从正文推测。"""
    return WRITING_RESEARCH_HISTORY.validate_json(task.writing_research_json or "[]")


def current_writing_research(task: ProductionTask) -> WritingResearchSnapshot | None:
    """优先本任务最新保存点，否则复用冻结输入内的已提交底稿。"""
    versions = writing_research_versions(task)
    return (
        versions[-1] if versions else ProductionContext.model_validate_json(task.input_json).writing_research
    )


def writing_research_excerpts(
    task: ProductionTask, evidence: list[ResearchEvidence]
) -> list[EvidenceExcerpt]:
    """按实际保存窗口返回底稿出处，包括历史版本；不返回未引用全文或猜测缺失片段。"""
    snapshots = writing_research_versions(task)
    inherited = ProductionContext.model_validate_json(task.input_json).writing_research
    if inherited:
        snapshots = [inherited, *snapshots]
    sources = {source.id: source for source in evidence}
    excerpts = {}
    for snapshot in snapshots:
        outline_sources = [
            source
            for paragraph in (snapshot.research.outline.paragraphs if snapshot.research.outline else [])
            for source in paragraph.sources
        ]
        for finding in [*snapshot.research.findings, *snapshot.research.background, *outline_sources]:
            span = finding.source_span
            source = sources.get(finding.evidence_id)
            if source is not None and source.content_hash == span.content_hash:
                excerpt = EvidenceExcerpt(
                    evidence_id=source.id,
                    content_hash=span.content_hash,
                    start=span.start,
                    end=span.end,
                    text=source.text[span.start : span.end],
                )
                excerpts[(source.id, span.content_hash, span.start, span.end)] = excerpt
    return list(excerpts.values())


def save_writing_research(
    work: ProductionUnit,
    task_id: str,
    token: str,
    result: WritingResearch,
    expected_version: int,
) -> WritingResearchSnapshot:
    """准确任务书下追加版本，同内容重试幂等；调用者持有事务，网络不得进入本用例。"""
    task = work.tasks.get(task_id)
    require_lease(task, token, "writer")
    if task.phase != "draft-writing" or task.research_policy != "pitch-first-v1":
        raise ValueError("此任务未开放作者研究底稿")
    require_skill_reads(task)
    story = work.stories.get(task.story_id)
    if story.status != "active" or story.revision != task.expected_revision:
        raise ValueError("任务书已变化，不能保存过期写作底稿")
    validate_writing_research(result, bound_task_evidence(work, task))
    current = current_writing_research(task)
    if current and current.research == result:
        return current
    version = writing_research_cursor(task).write_expected_version
    if expected_version != version:
        raise ValueError("底稿版本已变化，请读取当前底稿再保存")
    snapshot = WritingResearchSnapshot(
        version=version + 1,
        task_id=task.id,
        story_id=story.id,
        brief_version=story.brief_version,
        angle=story.brief.angle,
        created_at=utcnow(),
        research=result,
    )
    history = [*writing_research_versions(task), snapshot]
    work.tasks.save(
        task.model_copy(
            update={
                "writing_research_json": WRITING_RESEARCH_HISTORY.dump_json(history).decode(),
                "updated_at": snapshot.created_at,
            }
        )
    )
    return snapshot


def require_writing_research(task: ProductionTask) -> WritingResearchSnapshot | None:
    """作者研究与写作交稿前须有准确任务书底稿；阻断缺口不得假称已解决。"""
    snapshot = current_writing_research(task)
    if task.research_policy != "pitch-first-v1":
        return snapshot
    context = ProductionContext.model_validate_json(task.input_json)
    story = context.story
    if snapshot is None:
        raise ValueError("交稿前须保存基于选题的作者研究底稿")
    if story is None or (snapshot.story_id, snapshot.brief_version, snapshot.angle) != (
        story.id,
        story.brief_version,
        story.brief.angle,
    ):
        raise ValueError("作者研究底稿不属于当前任务书")
    if any(gap.handling == "block" for gap in snapshot.research.gaps):
        raise ValueError("作者底稿仍有核心阻断，请补查或报告主编需重新判断，不能交稿")
    if (
        task.newsroom_skills is not None
        and task.newsroom_skills.outline_policy == "news-outline-v1"
        and snapshot.research.outline is None
    ):
        raise ValueError("交稿前须在作者研究底稿中保存新闻骨架及逐段材料依赖")
    return snapshot
