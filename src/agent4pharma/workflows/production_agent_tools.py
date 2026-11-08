"""每次 MCP 会话仅绑定一个制作任务和租约，角色不能调用其他角色的提交工具。"""

from typing import Annotated

from mcp.types import CallToolResult
from pydantic import BaseModel, Field

from agent4pharma.modules.content.article_editing import ArticleEditResult, TextReference
from agent4pharma.modules.content.background_research import BackgroundResearch, BackgroundResearchSnapshot
from agent4pharma.modules.content.draft_contracts import DraftSubmission, EvidenceExcerpt
from agent4pharma.modules.content.draft_revision import DraftRevisionSubmission
from agent4pharma.modules.content.research_views import ResearchItemRead
from agent4pharma.modules.content.writing_research import WritingResearch, WritingResearchSnapshot
from agent4pharma.modules.editorial.commission_contracts import EditorialDecision, EditorialDecisionSubmission
from agent4pharma.modules.materials.evidence_catalog import EvidenceCatalogEntry, catalog_entry
from agent4pharma.modules.publishing.figure_catalog import FigureSearchResult
from agent4pharma.modules.publishing.release_contracts import ChannelSubmission
from agent4pharma.modules.review.reader_review_contracts import (
    ReaderObservation,
    ReaderReviewReport,
    ReaderReviewResult,
)
from agent4pharma.modules.review.review_contracts import ReviewResult
from agent4pharma.runtime.skill_resources import SkillCatalogView, SkillResource
from agent4pharma.workflows.article_editing import ArticleEditReading
from agent4pharma.workflows.background_research import background_research_versions, save_background_research
from agent4pharma.workflows.evidence_delivery import ContentReadResult, EvidenceDelivery
from agent4pharma.workflows.production_evidence import citation_lookup_key, read_skill, task_evidence
from agent4pharma.workflows.production_figures import find_paper_figures, inspect_paper_figures
from agent4pharma.workflows.production_reading_view import ProductionReadingView, production_reading_view
from agent4pharma.workflows.production_tasks import require_lease
from agent4pharma.workflows.production_workbench import ProductionWorkbench
from agent4pharma.workflows.production_working_sets import (
    DocumentReadResponse,
    EvidenceReadResult,
    FactWorkingSet,
    LayoutWorkingSet,
    ResearchCatalogPage,
    SourceCatalogPage,
    TaskCheckpoint,
    TopicWorkingSet,
    WritingWorkingSet,
    read_research_item,
    research_catalog,
    source_catalog,
    stage_working_set,
    task_checkpoint,
)
from agent4pharma.workflows.reader_editing import (
    ReaderBriefContext,
    ReaderBriefView,
    ReaderDraftView,
    ReaderReviewContext,
    read_reader_review,
    reader_brief,
    reader_draft,
    reader_review_context,
    record_reader_observation,
    save_reader_observation,
    submit_reader_review,
)
from agent4pharma.workflows.revision_context import RevisionReadingView, revision_reading_view
from agent4pharma.workflows.revision_workspace import RevisionEntry, RevisionWorkspaceTools
from agent4pharma.workflows.writing_research import save_writing_research, writing_research_cursor


class EvidenceLookup(BaseModel):
    """一项绑定原文定位请求。"""

    evidence_id: str
    query: str = Field(min_length=2, max_length=200)
    ignore_case: bool = False


class ProductionAgentTools:
    """受任务和角色服务端验证的制作工具。"""

    def __init__(
        self, app: ProductionWorkbench, task_id: str, token: str, *, compact_receipts: bool = False
    ) -> None:
        self.app, self.task_id, self.token = app, task_id, token
        self.compact_receipts = compact_receipts
        self.evidence_delivery = EvidenceDelivery()

    def _deliver_evidence(self, result: EvidenceExcerpt, *, reread: bool = False) -> ContentReadResult:
        task = self.app.execution.get(self.task_id)
        require_lease(task, self.token, task.role)
        source = next((s for s in task_evidence(task) if s.id == result.evidence_id), None)
        if source is None:
            raise ValueError("交付来源不在绑定任务内")
        return self.evidence_delivery.deliver(source, result, reread=reread)

    def get_stage_context(self) -> TopicWorkingSet | WritingWorkingSet | FactWorkingSet | LayoutWorkingSet:
        """读取本节点入场工作集；研究与来源正文通过专用工具按需取得。"""
        with self.app.unit() as work:
            task = work.tasks.get(self.task_id)
            require_lease(task, self.token, task.role)
            return stage_working_set(work, task)

    def list_task_sources(self, cursor: str = "") -> SourceCatalogPage:
        """只返回当前任务允许的十项来源目录，以next_cursor续页。"""
        with self.app.read_unit() as work:
            task = work.tasks.get(self.task_id)
            require_lease(task, self.token, task.role)
            return source_catalog(work, task, cursor)

    def get_task_checkpoint(self) -> TaskCheckpoint:
        """取得准确保存点和写游标；不重复返回稿件或研究正文。"""
        task = self.app.execution.get(self.task_id)
        require_lease(task, self.token, task.role)
        return task_checkpoint(task)

    def list_research_items(self, cursor: str = "") -> ResearchCatalogPage:
        """列出研究条目的准确版本ref，单项读取不带其他文档。"""
        task = self.app.execution.get(self.task_id)
        require_lease(task, self.token, task.role)
        return research_catalog(task, cursor)

    def read_research_item(self, ref: str) -> ResearchItemRead:
        """读取目录中一个准确研究条目；过期或越界引用拒绝。"""
        task = self.app.execution.get(self.task_id)
        require_lease(task, self.token, task.role)
        return read_research_item(task, ref)

    def get_article_edit_context(self) -> ArticleEditReading:
        """读取本次编辑要求、引用、准确正文与绑定证据。"""
        from agent4pharma.workflows.article_editing import article_edit_context

        return article_edit_context(self.app, self.task_id, self.token)

    def find_article_text(self, text: str, target: str = "body") -> list[TextReference]:
        """取得当前正文中指定文字的准确位置。"""
        from agent4pharma.workflows.article_editing import find_article_text

        return find_article_text(self.app, self.task_id, self.token, text, target)

    def submit_article_edit(self, result: ArticleEditResult) -> str:
        """按冻结编辑模式保存候选或直接建立新图文版本。"""
        from agent4pharma.workflows.article_editing import submit_article_edit

        return submit_article_edit(self.app, self.task_id, self.token, result)

    def get_reader_draft(self) -> ReaderDraftView:
        """只读当前准确成稿，首次阅读不提供任务书、底稿或已有事实通过意见。"""
        with self.app.unit() as work:
            return reader_draft(work, self.task_id, self.token)

    def get_reader_review_context(self) -> ReaderReviewContext:
        """返回完整盲读成稿与冻结必读资源；上下文缺失时可重读。"""
        with self.app.unit() as work:
            return reader_review_context(work, self.task_id, self.token)

    def record_reader_observation(self, result: ReaderObservation | None = None) -> ReaderBriefContext:
        """保存不可覆盖的首次观察并返回任务书；已保存时可省略结果以恢复。"""
        with self.app.unit() as work:
            return record_reader_observation(work, self.task_id, self.token, result)

    def save_reader_observation(self, result: ReaderObservation) -> str:
        """保存一次不可覆盖的阅读结果，然后继续同一评审任务。"""
        with self.app.unit() as work:
            return save_reader_observation(work, self.task_id, self.token, result)

    def read_reader_review(self, report_id: str) -> ReaderReviewReport:
        """按需展开本任务目录中的一个历史阅读报告，不能绕过首次盲读。"""
        with self.app.unit() as work:
            return read_reader_review(work, self.task_id, self.token, report_id)

    def get_reader_brief(self) -> ReaderBriefView:
        """首次观察后读取任务书与前轮阅读问题，不提供作者自审结论。"""
        with self.app.unit() as work:
            return reader_brief(work, self.task_id, self.token)

    def submit_reader_review(self, result: ReaderReviewResult) -> str:
        """保存阅读品质结论，按冻结策略排队有限返修或独立事实核验。"""
        with self.app.unit() as work:
            return submit_reader_review(work, self.app.definitions, self.task_id, self.token, result)

    def get_revision_context(self) -> RevisionReadingView | RevisionEntry:
        """一次交付准确返修工作集及冻结规范，记录宿主已准备的必读资源。"""
        if self.app.execution.get(self.task_id).context_contract == "node-context-v2":
            return RevisionWorkspaceTools(self.app, self.task_id, self.token).get_revision_context()
        context = self.app.context(self.task_id, self.token)
        with self.app.unit() as work:
            task = work.tasks.get(self.task_id)
            require_lease(task, self.token, "writer")
            view = revision_reading_view(context, task)
            if task.context_contract:
                view.sources = source_catalog(work, task)
                view.checkpoint = task_checkpoint(task)
            for resource in task.required_resources:
                read_skill(work, work.tasks.get(self.task_id), resource)
        return view

    def submit_draft_revision(self, result: DraftRevisionSubmission) -> str:
        """将准确版本补丁原子合成为新稿，沿用全部交稿校验并排队独立审核。"""
        return self.app.handoff.content(self.task_id, self.token, result)

    def get_production_context(self, recovery_only: bool = False) -> ProductionReadingView:
        """读取绑定任务；recovery_only 恢复任务书、保存点、稿件与审核，不重传证据目录。"""
        task = self.app.execution.get(self.task_id)
        require_lease(task, self.token, task.role)
        if task.context_contract:
            raise ValueError("CONTEXT_SCOPE_DENIED: 新契约使用节点工作集与分项读取，整包入口已关闭")
        context = self.app.context(self.task_id, self.token)
        if task.phase == "reading-review":
            raise ValueError("阅读评审使用get_reader_draft与get_reader_brief分步读取")
        if recovery_only:
            context = context.model_copy(update={"evidence": [], "candidates": []})
        view = production_reading_view(context, max(0, task.limits.max_reads - task.reads))
        if task.role == "reviewer" and context.draft:
            with self.app.unit() as work:
                edited = next(
                    (
                        p
                        for p in reversed(work.releases.packages())
                        if p.draft_id == context.draft.id and p.edit_pending
                    ),
                    None,
                )
                if edited and edited.presentation:
                    view.publication_figures = [f.selection for f in edited.presentation.figures]
        background = background_research_versions(task)
        view.background_research_expected_version = background[-1].version if background else 0
        view.writing_research_expected_version = writing_research_cursor(task).write_expected_version
        view.missing_required_resources = sorted(set(task.required_resources) - set(task.skill_reads))
        if task.newsroom_policy == "news-focus-v2":
            view.citation_lookups_remaining = max(0, 32 - len(task.citation_lookups))
        return view

    def read_document(
        self,
        evidence_id: str,
        start_page: int = 1,
        end_page: int = 1,
        after: str = "",
        reread: bool = False,
    ) -> DocumentReadResponse | ContentReadResult:
        """读取绑定PDF的1至3页；新契约只交付新增窗口，reread明确重新加载连续原文。"""
        result = self.app.read_document(self.task_id, self.token, evidence_id, start_page, end_page, after)
        task = self.app.execution.get(self.task_id)
        if task.context_contract != "node-context-v2" or result.status != "available":
            return DocumentReadResponse.model_validate(result.model_dump())
        source = next(s for s in task_evidence(task) if s.id == result.evidence_id)
        delivered = self._deliver_evidence(
            EvidenceExcerpt(
                evidence_id=source.id,
                content_hash=source.content_hash,
                start=0,
                end=len(source.text),
                text=source.text,
            ),
            reread=reread,
        )
        return delivered.model_copy(
            update={
                "locators": result.locators,
                "truncated": result.truncated,
                "next_request": result.next_request,
            }
        )

    def list_newsroom_skills(self) -> SkillCatalogView:
        """只返回本任务冻结的技能目录；每次检查租约，不访问当前安装版本。"""
        self.app.context(self.task_id, self.token)
        bundle = self.app.execution.get(self.task_id).newsroom_skills
        if bundle is None:
            raise ValueError("历史任务未绑定新闻技能包")
        return SkillCatalogView(
            version=bundle.version, content_hash=bundle.content_hash, skills=bundle.skills
        )

    def read_newsroom_resource(self, resource: str) -> SkillResource:
        """读取冻结技能文件，按完整资源名白名单匹配；不占论文读取额度。"""
        with self.app.unit() as work:
            task = work.tasks.get(self.task_id)
            require_lease(task, self.token, task.role)
            return read_skill(work, task, resource)

    def read_evidence(
        self,
        evidence_id: str,
        start: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1)] = 3000,
        reread: bool = False,
    ) -> EvidenceReadResult | ContentReadResult:
        """最多读取6000字符；新契约返回新增窗口与已读引用，reread用于明确重读。"""
        result = self.app.read_evidence(self.task_id, self.token, evidence_id, start, min(limit, 6000))
        if self.app.execution.get(self.task_id).context_contract == "node-context-v2":
            return self._deliver_evidence(result, reread=reread)
        return EvidenceReadResult.model_validate(result.model_dump())

    def search_local_materials(self, query: str) -> list[EvidenceCatalogEntry]:
        """有限检索本地题录，结果作为实际补充证据保存。"""
        return [catalog_entry(e) for e in self.app.research(self.task_id, self.token, query)]

    def read_bound_source(self, url: str) -> list[EvidenceCatalogEntry]:
        """补读任务资料已有 URL；不能访问任意网站。"""
        return [catalog_entry(e) for e in self.app.research(self.task_id, self.token, url, url=True)]

    def submit_editorial_decision(self, result: EditorialDecision) -> str:
        """按旧任务冻结契约提交选题决定。"""
        return self.app.handoff.editorial(self.task_id, self.token, result)

    def submit_current_editorial_decision(self, result: EditorialDecisionSubmission) -> str:
        """提交报道或不报道，程序校验候选和任务书后交接。"""
        return self.submit_editorial_decision(EditorialDecision(**result.model_dump()))

    def find_bound_evidence(
        self,
        evidence_id: str,
        query: str,
        ignore_case: bool = False,
        compact: bool = False,
        reread: bool = False,
    ) -> list[EvidenceExcerpt] | list[ContentReadResult]:
        """定位绑定原文；compact 仅返回命中附近短片段，保留准确位置及版本。"""
        results = self.app.find_evidence(
            self.task_id, self.token, evidence_id, query, ignore_case=ignore_case, compact=compact
        )
        if self.app.execution.get(self.task_id).context_contract == "node-context-v2":
            return [self._deliver_evidence(result, reread=reread) for result in results]
        return results

    def find_bound_evidence_batch(
        self,
        queries: Annotated[list[EvidenceLookup], Field(min_length=1, max_length=8)],
        reread: bool = False,
    ) -> list[list[EvidenceExcerpt]] | list[list[ContentReadResult]]:
        """按顺序批量返回短引文；额度不足整批预先拒绝，避免部分扣额而丢失结果。"""
        task = self.app.execution.get(self.task_id)
        require_lease(task, self.token, task.role)
        if task.newsroom_policy == "news-focus-v2":
            keys = {citation_lookup_key(q.evidence_id, q.query, q.ignore_case) for q in queries}
            remaining = max(0, 32 - len(task.citation_lookups))
            if len(keys - set(task.citation_lookups)) > remaining:
                raise ValueError(
                    f"本地引用定位剩余{remaining}个不同查询，本批未执行；请缩小批次或复用已有窗口"
                )
        results = [
            self.app.find_evidence(
                self.task_id,
                self.token,
                item.evidence_id,
                item.query,
                ignore_case=item.ignore_case,
                compact=True,
            )
            for item in queries
        ]
        if task.context_contract == "node-context-v2":
            return [[self._deliver_evidence(result, reread=reread) for result in row] for row in results]
        return results

    def submit_draft(self, result: DraftSubmission) -> str:
        """纯JSON交稿；claims.kind仅fact/attributed_claim/inference，ID/hash逐字复制，禁止表达式。"""
        return self.app.handoff.content(self.task_id, self.token, result)

    def save_writing_research(
        self, result: WritingResearch, expected_version: int = 0, receipt_only: bool | None = None
    ) -> WritingResearchSnapshot | int:
        """保存底稿；新契约简短回执为下一写游标，继承底稿版本不作为本任务写游标。"""
        with self.app.unit() as work:
            saved = save_writing_research(work, self.task_id, self.token, result, expected_version)
            task = work.tasks.get(self.task_id)
            cursor = writing_research_cursor(task).write_expected_version
        compact = (self.compact_receipts if receipt_only is None else receipt_only) or (
            bool(task.context_contract) and self.compact_receipts
        )
        return (cursor if task.context_contract else saved.version) if compact else saved

    def submit_review(self, result: ReviewResult) -> str:
        """编辑提交精确版本结论，不能修改作者的稿件。"""
        return self.app.handoff.review(self.task_id, self.token, result)

    def save_background_research(
        self, result: BackgroundResearch, expected_version: int = 0, receipt_only: bool | None = None
    ) -> BackgroundResearchSnapshot | int:
        """保存背景调研；expected_version取当前任务专用字段，首次0，上游调研版本不计入。"""
        with self.app.unit() as work:
            saved = save_background_research(work, self.task_id, self.token, result, expected_version)
            task = work.tasks.get(self.task_id)
        compact = (self.compact_receipts if receipt_only is None else receipt_only) or (
            bool(task.context_contract) and self.compact_receipts
        )
        return saved.version if compact else saved

    def find_paper_figures(self, evidence_id: str, query: str, page: int = 0) -> FigureSearchResult:
        """关键词定位相关图页及子图ID；最多6次不同查询，不返回全文；page可精确限定。"""
        return find_paper_figures(
            self.app.unit, self.app.figure_catalog, self.task_id, self.token, evidence_id, query, page
        )

    def inspect_paper_figures(self, evidence_id: str, figure_ids: list[str]) -> CallToolResult:
        """返回真实图像供视觉核对；pN为标有子图ID的页内缩略图索引、pNbM为原尺寸子图，每批最多3图、总计6图。"""
        return inspect_paper_figures(
            self.app.unit, self.app.figure_inspector, self.task_id, self.token, evidence_id, figure_ids
        )

    def submit_channel_package(self, result: ChannelSubmission) -> str:
        """发行只选择已接入本地渠道，不具备外部账号发送工具。"""
        return self.app.submit_package(self.task_id, self.token, result)

    def report_production_blocked(self, reason: str) -> str:
        """缺证据/范围变化时明确等待处理，不能用自由文本冒充完成。"""
        self.app.blocked(self.task_id, self.token, reason)
        return "blocked"
