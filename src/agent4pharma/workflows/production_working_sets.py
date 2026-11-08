"""按准确任务组装节点工作集、恢复游标与有界目录；不执行模型或改变业务产物。"""

import hashlib

from pydantic import Field

from agent4pharma.modules.content.draft_contracts import EvidenceExcerpt
from agent4pharma.modules.content.research_views import ResearchItem, ResearchItemRead, research_items
from agent4pharma.modules.editorial.commission_contracts import Story
from agent4pharma.modules.materials.document_reading import DocumentEvidenceRead
from agent4pharma.modules.materials.evidence_catalog import EvidenceCatalogEntry, catalog_entry
from agent4pharma.modules.publishing.article_layout import FigureSelection
from agent4pharma.modules.review.review_contracts import ReviewReport
from agent4pharma.runtime.context_records import ContextPayload
from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.workflows.background_research import (
    background_research_reports,
    background_research_versions,
)
from agent4pharma.workflows.production_dependencies import ProductionContext, ProductionUnit
from agent4pharma.workflows.production_evidence import bound_task_evidence
from agent4pharma.workflows.production_reading_view import (
    CandidateReadingView,
    DraftReadingView,
    production_reading_view,
)
from agent4pharma.workflows.writing_research import (
    ResearchReadCursor,
    current_writing_research,
    writing_research_cursor,
)


class SourceCatalogPage(ContextPayload):
    """固定任务来源的有界目录，游标绑定集合 hash；不返回正文或定位器全集。"""

    items: list[EvidenceCatalogEntry]
    scope_hash: str
    next_cursor: str = ""
    has_more: bool = False


class TaskCheckpoint(ContextPayload):
    """恢复入口只提供保存点身份、版本和额度，不重复交付完整业务工作集。"""

    task_id: str
    phase: str
    draft_id: str
    draft_hash: str
    writing: ResearchReadCursor
    background_write_expected_version: int
    reads_remaining: int
    citation_lookups_remaining: int
    missing_required_resources: list[str]


class ResearchCatalogPage(ContextPayload):
    """研究条目目录按准确集合分页，正文通过准确 ref 单项读取。"""

    items: list[ResearchItem]
    next_cursor: str = ""
    has_more: bool = False


class TopicWorkingSet(ContextPayload):
    """主编仅取得候选与入选依据，研究正文按需读取。"""

    candidates: list[CandidateReadingView]
    checkpoint: TaskCheckpoint
    sources: SourceCatalogPage


class WritingWorkingSet(ContextPayload):
    """初稿作者入场取得准确任务书与来源入口，不加载历史研究正文。"""

    story: Story
    checkpoint: TaskCheckpoint
    sources: SourceCatalogPage


class FactWorkingSet(ContextPayload):
    """核验完整准确稿件和前轮事实问题，不暴露作者自评或阅读报告。"""

    story: Story
    draft: DraftReadingView
    reviews: list[ReviewReport]
    review_mode: str
    sources: SourceCatalogPage
    checkpoint: TaskCheckpoint
    publication_figures: list[FigureSelection] = Field(default_factory=list)


class LayoutWorkingSet(ContextPayload):
    """制作只取得准确稿件、渠道任务与图源目录，不重放研究或评审全文。"""

    draft: DraftReadingView
    layout_source_package: str
    sources: SourceCatalogPage


def require_context_access(task: ProductionTask, *, research: bool = False) -> None:
    """新读取工具按阶段拒绝盲读、人工改稿和图文阶段的越权研究。"""
    allowed = {"topic-selection", "draft-writing", "fact-check"}
    if not research:
        allowed.add("layout-production")
    if task.context_contract not in {"node-context-v1", "node-context-v2"} or task.phase not in allowed:
        raise ValueError("CONTEXT_SCOPE_DENIED: 当前阶段未开放此上下文入口")


def page_offset(cursor: str, digest: str, length: int) -> int:
    """目录变化拒绝旧游标，不把偏移悄悄应用到另一组资料。"""
    if not cursor:
        return 0
    head, separator, tail = cursor.partition(":")
    if head != digest or not separator or not tail.isdigit() or not 0 <= int(tail) <= length:
        raise ValueError("CONTEXT_VERSION_MISMATCH: 目录已变化，请从首页读取")
    return int(tail)


def source_catalog(work: ProductionUnit, task: ProductionTask, cursor: str = "") -> SourceCatalogPage:
    """每页最多十个来源，无稿件、研究正文和全量 locators。"""
    require_context_access(task)
    sources = bound_task_evidence(work, task)
    digest = hashlib.sha256("|".join(f"{s.id}:{s.content_hash}" for s in sources).encode()).hexdigest()
    offset = page_offset(cursor, digest, len(sources))
    end = min(len(sources), offset + 10)
    return SourceCatalogPage(
        items=[catalog_entry(s).model_copy(update={"locators": []}) for s in sources[offset:end]],
        scope_hash=digest,
        next_cursor=f"{digest}:{end}" if end < len(sources) else "",
        has_more=end < len(sources),
    )


def task_checkpoint(task: ProductionTask) -> TaskCheckpoint:
    """同一写游标供恢复与保存使用，返回中不包含正文。"""
    require_context_access(task)
    context = ProductionContext.model_validate_json(task.input_json)
    history = background_research_versions(task)
    return TaskCheckpoint(
        task_id=task.id,
        phase=task.phase,
        draft_id=context.draft.id if context.draft else "",
        draft_hash=context.draft.content_hash if context.draft else "",
        writing=writing_research_cursor(task),
        background_write_expected_version=history[-1].version if history else 0,
        reads_remaining=max(0, task.limits.max_reads - task.reads),
        citation_lookups_remaining=max(0, 32 - len(task.citation_lookups)),
        missing_required_resources=sorted(set(task.required_resources) - set(task.skill_reads)),
    )


def bound_research_items(task: ProductionTask) -> list[ResearchItemRead]:
    """只解析任务实际保存或继承的研究；事实核验不提供作者自评字段。"""
    require_context_access(task, research=True)
    writing = current_writing_research(task)
    items = research_items(writing, "writing") if writing else []
    for report in background_research_reports(task):
        items.extend(research_items(report, "background"))
    if task.phase == "fact-check":
        items = [
            i
            for i in items
            if i.item.section.split("/")[0] in {"findings", "background", "questions", "gaps"}
        ]
    return list({item.item.ref: item for item in items}.values())


def research_catalog(task: ProductionTask, cursor: str = "") -> ResearchCatalogPage:
    """每页二十条元数据，不返回研究答案与来源正文。"""
    items = [item.item for item in bound_research_items(task)]
    digest = hashlib.sha256("|".join(item.ref for item in items).encode()).hexdigest()
    offset = page_offset(cursor, digest, len(items))
    end = min(len(items), offset + 20)
    return ResearchCatalogPage(
        items=items[offset:end],
        has_more=end < len(items),
        next_cursor=f"{digest}:{end}" if end < len(items) else "",
    )


def read_research_item(task: ProductionTask, ref: str) -> ResearchItemRead:
    """拒绝旧版本和其他任务的引用，不以最新研究替代。"""
    item = next((item for item in bound_research_items(task) if item.item.ref == ref), None)
    if item is None:
        raise ValueError("CONTEXT_VERSION_MISMATCH: 研究条目不属于当前准确保存点")
    return item


def stage_working_set(
    work: ProductionUnit, task: ProductionTask
) -> TopicWorkingSet | WritingWorkingSet | FactWorkingSet | LayoutWorkingSet:
    """入场返回节点专用 DTO，禁止用恢复参数绕回整包上下文。"""
    require_context_access(task)
    context = ProductionContext.model_validate_json(task.input_json)
    view = production_reading_view(context, max(0, task.limits.max_reads - task.reads))
    sources = source_catalog(work, task)
    if task.phase == "topic-selection":
        return TopicWorkingSet(candidates=view.candidates, checkpoint=task_checkpoint(task), sources=sources)
    if task.phase == "draft-writing":
        if context.draft is not None:
            raise ValueError("返修请使用get_revision_context")
        return WritingWorkingSet(story=context.story, checkpoint=task_checkpoint(task), sources=sources)
    if context.draft is None:
        raise ValueError("CONTEXT_REQUIRED_ITEM_MISSING: 缺少准确稿件")
    if task.phase == "fact-check":
        edited = next(
            (
                p
                for p in reversed(work.releases.packages())
                if p.draft_id == context.draft.id and p.edit_pending
            ),
            None,
        )
        return FactWorkingSet(
            story=context.story,
            draft=view.draft,
            reviews=context.reviews,
            review_mode=context.review_mode,
            sources=sources,
            checkpoint=task_checkpoint(task),
            publication_figures=[f.selection for f in edited.presentation.figures]
            if edited and edited.presentation
            else [],
        )
    return LayoutWorkingSet(
        draft=view.draft, layout_source_package=context.layout_source_package, sources=sources
    )


class EvidenceReadResult(EvidenceExcerpt, ContextPayload):
    """原文窗口工具返回的传输投影，回执不写入业务证据。"""


class DocumentReadResponse(DocumentEvidenceRead, ContextPayload):
    """PDF读取的传输投影，与持久化缓存回执分离。"""
