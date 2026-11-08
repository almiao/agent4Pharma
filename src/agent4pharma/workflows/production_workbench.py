"""人工制作入口、候选快照和生产投影；所有执行复用持久化工作单元。"""

import hashlib
import re
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager

from pydantic import BaseModel, Field

from agent4pharma.modules.content.draft_contracts import EvidenceExcerpt, ResearchEvidence
from agent4pharma.modules.editorial.commission_contracts import Story
from agent4pharma.modules.materials.document_reading import (
    DocumentEvidenceRead,
    DocumentPreparation,
    DocumentReadResult,
    DocumentReference,
)
from agent4pharma.modules.publishing.article_layout import FigureReader, LayoutRequest
from agent4pharma.modules.publishing.figure_catalog import FigureCatalogReader, FigureInspector
from agent4pharma.modules.publishing.human_approval import package_fingerprint
from agent4pharma.modules.publishing.paper_header import PaperHeaderReader
from agent4pharma.modules.publishing.release_contracts import (
    ChannelPackage,
    ChannelSubmission,
    ReleaseReceipt,
)
from agent4pharma.runtime.production_execution import ProductionLimits, ProductionRole, ProductionTask
from agent4pharma.runtime.workflow_observation import ObservationSink
from agent4pharma.shared.clock import utcnow
from agent4pharma.workflows.background_research import background_research_reports
from agent4pharma.workflows.human_package_approval import require_reviewable_package
from agent4pharma.workflows.production_dependencies import (
    CandidateSnapshot,
    ProductionContext,
    ProductionUnit,
)
from agent4pharma.workflows.production_evidence import (
    bound_task_evidence,
    citation_lookup_key,
    save_task_evidence,
    task_evidence,
)
from agent4pharma.workflows.production_handoff import ProductionHandoff
from agent4pharma.workflows.production_paper_flows import ProductionPaperFlow, production_paper_flows
from agent4pharma.workflows.production_queue import (
    ProductionRoleDefinition,
    PublishedRoleDefinitions,
    queue_role,
)
from agent4pharma.workflows.production_release_validation import require_current_package
from agent4pharma.workflows.production_story_state import active_story
from agent4pharma.workflows.production_tasks import ProductionExecution, require_lease
from agent4pharma.workflows.production_views import ProductionArtifactIndex, summarize_production_artifacts
from agent4pharma.workflows.publication_layout import create_article_layout
from agent4pharma.workflows.writing_research import current_writing_research


class ProductionSnapshot(BaseModel):
    """生产台读模型；没有记录为空，不捏造任务和调用效果。"""

    as_of: str
    stories: list[Story]
    tasks: list[ProductionTask]
    contexts: list[ProductionContext] = Field(default_factory=list)
    paper_flows: list[ProductionPaperFlow] = Field(default_factory=list)


class ProductionWorkbench:
    """四角色制作应用，持久化排队与人工操作不自动启动付费调用。"""

    def __init__(
        self,
        unit: Callable[[], AbstractContextManager[ProductionUnit]],
        candidates: Callable[[list[str]], list[CandidateSnapshot]],
        definitions: Mapping[str, ProductionRoleDefinition],
        search: Callable[[str], list[ResearchEvidence]],
        reader: Callable[[str], ResearchEvidence],
        document_reader: Callable[[DocumentReference, int, int, str], DocumentReadResult] | None = None,
        document_preparer: Callable[[DocumentReference], DocumentPreparation] | None = None,
        cached_document_reader: Callable[[DocumentReference, int, int, str], DocumentReadResult]
        | None = None,
        figure_reader: FigureReader | None = None,
        figure_catalog: FigureCatalogReader | None = None,
        figure_inspector: FigureInspector | None = None,
        read_unit: Callable[[], AbstractContextManager[ProductionUnit]] | None = None,
        header_reader: PaperHeaderReader | None = None,
    ) -> None:
        self.unit, self.candidates, self.definitions = unit, candidates, definitions
        self.read_unit = read_unit or unit
        self.search, self.reader = search, reader
        self.figure_catalog, self.figure_inspector = figure_catalog, figure_inspector
        self.figure_reader = figure_reader
        self.header_reader = header_reader
        self.document_reader = document_reader
        self.document_preparer = document_preparer
        self.cached_document_reader = cached_document_reader
        self.execution = ProductionExecution(unit, self.read_unit)
        self.handoff = ProductionHandoff(unit, definitions)

    def start(self, candidate_ids: list[str], limits: ProductionLimits) -> ProductionTask:
        """显式选取正式候选交主编选题；论文深入理解由立项后的作者承担。"""
        if not candidate_ids or len(candidate_ids) > 20 or len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("每次请选择 1..20 个不重复的正式候选")
        candidates = self.candidates(candidate_ids)
        if {candidate.id for candidate in candidates} != set(candidate_ids):
            raise ValueError("存在失效或非正式推荐候选")
        pipeline = self.definitions["writer"].newsroom_pipeline
        with self.unit() as work:
            task = queue_role(
                work,
                self.definitions,
                "editor",
                ProductionContext(candidates=candidates, phase="topic-selection" if pipeline else ""),
                limits,
            )
            lineage = {task.id}
            for queued in sorted(work.tasks.list(), key=lambda t: t.created_at):
                if queued.parent_task_id in lineage:
                    lineage.add(queued.id)
            if any(
                set(story.candidate_ids) & set(candidate_ids)
                and story.id not in {"story-" + i for i in lineage}
                for story in work.stories.list()
            ):
                raise ValueError("候选已有选题，请查看原选题或显式修订任务书")
            return task

    def next_task(self, result: ProductionTask) -> ProductionTask | None:
        """新任务按直接父子关系续跑；旧任务仍按选题查找，不扩大授权次数。"""
        tasks = self.snapshot().tasks
        if result.phase:
            return next((t for t in tasks if t.parent_task_id == result.id and t.status == "queued"), None)
        story_id = result.story_id or result.result_ref
        return next((t for t in tasks if t.story_id == story_id and t.status == "queued"), None)

    def prepare_documents(self, task_id: str) -> list[DocumentPreparation]:
        """宿主显式准备绑定原文缓存；不领取租约、不调用模型、不将正文送入上下文。"""
        task = self.execution.get(task_id)
        if self.document_preparer is None:
            return []
        with self.unit() as work:
            sources = bound_task_evidence(work, task)
        documents = {e.document.sha256: e.document for e in sources if e.document}
        return [self.document_preparer(document) for document in documents.values()]

    def snapshot(self) -> ProductionSnapshot:
        """取得同一数据库工作单元上的当前选题和任务清单。"""
        with self.read_unit() as work:
            tasks = work.tasks.list()
            stories = work.stories.list()
            return ProductionSnapshot(
                as_of=utcnow(),
                stories=stories,
                tasks=tasks,
                paper_flows=production_paper_flows(tasks, {s.id for s in stories}),
            )

    def stage_models(self) -> dict[str, str]:
        """读取装配时的角色模型，浏览制作清单无需加载排队用的发布正文。"""
        definitions = self.definitions
        if isinstance(definitions, PublishedRoleDefinitions):
            definitions = definitions.defaults
        return {role: definition.model for role, definition in definitions.items()}

    def artifact_summaries(self, observations: ObservationSink | None = None) -> ProductionArtifactIndex:
        """读取产物身份与版本供中控抽查；正文和执行操作仍在所属工序中处理。"""
        with self.read_unit() as work:
            return summarize_production_artifacts(work, observations)

    def context(self, task_id: str, token: str) -> ProductionContext:
        """模型只能读取绑定任务；内容补读实际资料另存，不静默改初始请求。"""
        with self.unit() as work:
            task = work.tasks.get(task_id)
            require_lease(task, token, task.role)
            context = ProductionContext.model_validate_json(task.input_json)
            context.writing_research = current_writing_research(task)
            context.background_research = background_research_reports(task)
            if task.story_id and task.role == "writer" and task.phase != "article-revision":
                self._require_research_version(work, task)
                context.evidence = bound_task_evidence(work, task)
            elif task.phase == "topic-selection":
                context.evidence = task_evidence(task)
            return context

    def research(self, task_id: str, token: str, query: str, *, url: bool = False) -> list[ResearchEvidence]:
        """内容在授权选题内检索本地资料或补读已有 URL；获取在事务外且受次数限制。"""
        if not 2 <= len(query.strip()) <= 500:
            raise ValueError("研究查询长度须为 2..500")
        with self.unit() as work:
            task = work.tasks.get(task_id)
            require_lease(task, token, "writer")
            if task.story_id:
                self._require_research_version(work, task)
            else:
                raise ValueError("当前任务不能执行研究")
            sources = bound_task_evidence(work, task)
            if url and query not in {e.url for e in sources}:
                raise ValueError("只允许读取本任务资料中已登记的 URL")
            if url:
                cached = [
                    e
                    for e in sources
                    if e.origin == "web_background_page" and (e.url == query or query in e.locators)
                ]
                if cached:
                    return cached
            if task.reads >= task.limits.max_reads:
                raise ValueError("研究读取额度耗尽")
            work.tasks.save(task.model_copy(update={"reads": task.reads + 1}))
        results = [self.reader(query)] if url else self.search(query)
        if url and any(e.url == query and e.origin == "web_search_snippet" for e in sources):
            results = [
                e.model_copy(update={"origin": "web_background_page", "locators": [query, *e.locators]})
                for e in results
            ]
        with self.unit() as work:
            task = work.tasks.get(task_id)
            require_lease(task, token, "writer")
            if task.story_id:
                self._require_research_version(work, task)
                for evidence in results:
                    work.drafts.add_evidence(task.story_id, evidence)
            if task.context_contract or not task.story_id:
                save_task_evidence(work, task, results)
        return results

    def register_search_results(self, task_id: str, token: str, evidence: list[ResearchEvidence]) -> None:
        """接收宿主 DSH 的实际搜索结果，不接受模型自行声明任意 URL 已获验证。"""
        with self.unit() as work:
            task = work.tasks.get(task_id)
            require_lease(task, token, task.role)
            pitch_first = task.research_policy == "pitch-first-v1" and (task.role, task.phase) in {
                ("editor", "topic-selection"),
                ("writer", "draft-writing"),
                ("writer", "article-revision"),
            }
            if not task.web_search_enabled or not pitch_first:
                raise ValueError("此任务未开放网页搜索")
            if task.story_id:
                self._require_research_version(work, task)
                for source in evidence:
                    work.drafts.add_evidence(task.story_id, source)
            save_task_evidence(work, task, evidence)

    def blocked(self, task_id: str, token: str, reason: str) -> None:
        """缺资料或重大变更先保存阻断，不冒充失败或完成。"""
        if not 10 <= len(reason.strip()) <= 3000:
            raise ValueError("请说明具体阻断原因")
        with self.unit() as work:
            task = work.tasks.get(task_id)
            require_lease(task, token, task.role)
            work.tasks.save(
                task.model_copy(
                    update={
                        "status": "failed" if task.auto_run else "blocked",
                        "error": reason,
                        "updated_at": utcnow(),
                    }
                )
            )

    def find_evidence(
        self,
        task_id: str,
        token: str,
        evidence_id: str,
        query: str,
        *,
        ignore_case: bool = False,
        compact: bool = False,
    ) -> list[EvidenceExcerpt]:
        """有界检索绑定证据的连续原文，逐字保留空白与标点；新策略的本地定位不扣材料读取额度。"""
        if not 2 <= len(query.strip()) <= 200:
            raise ValueError("证据定位查询需为 2..200 字符")
        task = self.execution.get(task_id)
        if task.role not in {"writer", "reviewer"} and not (
            task.role == "editor"
            and task.phase == "topic-selection"
            and task.research_policy == "pitch-first-v1"
        ):
            raise ValueError("仅作者与编辑可定位绑定证据")
        lookup_key = citation_lookup_key(evidence_id, query, ignore_case)
        _, evidence = self._reading_source(task_id, token, evidence_id, lookup_key=lookup_key)
        # PDF 提取的换行和连续空格不应导致反复无结果；返回仍是原文切片，引用校验不放宽。
        pattern = r"\s+".join(re.escape(word) for word in query.split())
        results = []
        before, after = (80, 160) if compact else (120, 480)
        for match in re.finditer(pattern, evidence.text, re.IGNORECASE if ignore_case else 0):
            start, end = max(0, match.start() - before), min(len(evidence.text), match.end() + after)
            results.append(
                EvidenceExcerpt(
                    evidence_id=evidence.id,
                    content_hash=evidence.content_hash,
                    start=start,
                    end=end,
                    text=evidence.text[start:end],
                )
            )
            if len(results) == 3:
                break
        return results

    def _require_research_version(self, work: ProductionUnit, task: ProductionTask) -> None:
        if task.phase == "article-revision" and task.web_search_enabled:
            from agent4pharma.workflows.article_product_versions import bound_product

            context = ProductionContext.model_validate_json(task.input_json)
            bound_product(work, context.article_edit.package_id)
        else:
            active_story(work, task)

    def _reading_source(
        self, task_id: str, token: str, evidence_id: str, *, lookup_key: str = ""
    ) -> tuple[ProductionTask, ResearchEvidence]:
        with self.unit() as work:
            task = work.tasks.get(task_id)
            require_lease(task, token, task.role)
            if task.role == "publisher" or task.phase == "reading-review":
                raise ValueError("发行角色只读取已审核稿件，不重新调查论文")
            if task.story_id:
                self._require_research_version(work, task)
                sources = bound_task_evidence(work, task)
            else:
                sources = task_evidence(task)
            source = next((e for e in sources if e.id == evidence_id), None)
            if source is None:
                raise ValueError("证据不属于绑定任务")
            if lookup_key and task.newsroom_policy == "news-focus-v2":
                if lookup_key not in task.citation_lookups:
                    if len(task.citation_lookups) >= 32:
                        raise ValueError("本地引用定位已达32个不同查询，请复用已有窗口")
                    work.tasks.save(
                        task.model_copy(update={"citation_lookups": (*task.citation_lookups, lookup_key)})
                    )
            else:
                if task.reads >= task.limits.max_reads:
                    raise ValueError("研究读取额度耗尽")
                work.tasks.save(task.model_copy(update={"reads": task.reads + 1}))
            return task, source

    def read_evidence(
        self, task_id: str, token: str, evidence_id: str, start: int = 0, limit: int = 3000
    ) -> EvidenceExcerpt:
        """读取一个明确证据的小片段，不在初始上下文中返回全文。"""
        if start < 0 or not 1 <= limit <= 6000:
            raise ValueError("每次读取1至6000字符，起点不能为负")
        _, source = self._reading_source(task_id, token, evidence_id)
        if start >= len(source.text):
            raise ValueError("字符位置超出证据范围")
        end = min(len(source.text), start + limit)
        return EvidenceExcerpt(
            evidence_id=source.id,
            content_hash=source.content_hash,
            start=start,
            end=end,
            text=source.text[start:end],
        )

    def read_document(
        self, task_id: str, token: str, evidence_id: str, start_page: int, end_page: int, after: str = ""
    ) -> DocumentEvidenceRead:
        """按需读取至多三页；登记片段及不含正文的去重回执。"""
        if not 1 <= start_page <= end_page or end_page - start_page >= 3:
            raise ValueError("每次仅可按需读取1至3页")
        current = self.execution.get(task_id)
        require_lease(current, token, current.role)
        if (
            current.newsroom_policy
            and current.phase not in {"draft-writing", "fact-check", "article-revision"}
            and not (current.phase == "topic-selection" and current.research_policy == "pitch-first-v1")
        ):
            raise ValueError("当前阶段摘要优先，不开放PDF全文阅读；具体缺口交给写作阶段")
        request_key = f"{evidence_id}:{start_page}:{end_page}:{after}"
        if request_key in current.document_reads:
            saved = DocumentEvidenceRead.model_validate_json(current.document_reads[request_key])
            return saved.model_copy(
                update={
                    "text": "",
                    "already_read": True,
                    "reads_remaining": max(0, current.limits.max_reads - current.reads),
                    "next_action": (
                        "复用 evidence_id；必要时 read_evidence 读取局部，不重复解析。"
                        if saved.status == "available"
                        else saved.next_action
                    ),
                }
            )
        task, source = self._reading_source(task_id, token, evidence_id)
        if task.role not in {"writer", "reviewer"} and not (
            task.role == "editor" and task.research_policy == "pitch-first-v1"
        ):
            raise ValueError("仅写作与审核可读取绑定PDF页")
        if source.document is None or self.document_reader is None:
            raise ValueError("此证据没有已关联的MinerU文档")
        reader = self.document_reader
        if task.newsroom_policy:
            reader = self.cached_document_reader or self.document_reader
            if task.phase in {"draft-writing", "fact-check"}:
                reader = self.document_reader
        result = reader(source.document, start_page, end_page, after)
        if result.status != "available" or result.content is None:
            response = DocumentEvidenceRead(
                status=result.status,
                error_code=result.error_code,
                next_action=(
                    "正文页暂不可用。停止重复请求此范围；使用已有证据继续，必要时记录具体缺口。"
                    if result.status == "pending"
                    else "正文读取技术失败或服务不可用。保留错误码，使用已有材料继续；不推断论文缺失该信息。"
                ),
            )
            with self.unit() as work:
                current = work.tasks.get(task_id)
                require_lease(current, token, task.role)
                work.tasks.save(
                    current.model_copy(
                        update={
                            "document_reads": {
                                **current.document_reads,
                                request_key: response.model_dump_json(exclude={"text"}),
                            }
                        }
                    )
                )
            return response
        text = result.content.content
        identity = hashlib.sha256(
            f"{source.document.sha256}:{start_page}:{end_page}:{after}:{text}".encode()
        ).hexdigest()
        evidence = ResearchEvidence(
            id="mineru-" + identity[:32],
            title=source.title,
            text=text,
            url=source.url,
            origin="primary_fulltext_excerpt",
            fulltext=True,
            document=source.document,
            read_scope=f"PDF页{start_page}-{end_page}; truncated={result.content.truncated}",
            locators=result.locators,
        )
        response = DocumentEvidenceRead(
            status="available",
            evidence_id=evidence.id,
            content_hash=evidence.content_hash,
            text=text,
            locators=result.locators,
            truncated=result.content.truncated,
            next_request=result.content.next_request,
            reads_remaining=max(0, current.limits.max_reads - current.reads),
        )
        with self.unit() as work:
            current = work.tasks.get(task_id)
            require_lease(current, token, task.role)
            if current.story_id:
                self._require_research_version(work, current)
                work.drafts.add_evidence(task.story_id, evidence)
            if current.context_contract or not current.story_id:
                save_task_evidence(work, current, [evidence])
                current = work.tasks.get(task_id)
            work.tasks.save(
                current.model_copy(
                    update={
                        "document_reads": {
                            **current.document_reads,
                            request_key: response.model_dump_json(exclude={"text"}),
                        }
                    }
                )
            )
        return response

    def resume(self, story_id: str, role: ProductionRole, limits: ProductionLimits) -> ProductionTask:
        """排队独立编辑复议，旧结论与冻结输入不覆盖。"""
        if role != "reviewer":
            raise ValueError("仅支持独立编辑复议；制作恢复请操作准确任务")
        with self.unit() as work:
            story = work.stories.get(story_id)
            if story.status == "cancelled":
                raise ValueError("已取消选题不能恢复")
            if any(
                t.story_id == story_id and t.status in {"queued", "running", "paused"}
                for t in work.tasks.list()
            ):
                raise ValueError("此选题已有待执行任务")
            story = self._revise(work, story, "人工独立复议", status="active")
            drafts = [d for d in work.drafts.list() if d.story_id == story_id]
            if not drafts:
                raise ValueError("没有可复议稿件")
            reviews = [r for r in work.reviews.list() if drafts and r.draft_id == drafts[-1].id]
            previous = sorted(
                (t for t in work.tasks.list() if t.story_id == story_id), key=lambda t: t.created_at
            )
            prior = (
                ProductionContext.model_validate_json(previous[-1].input_json)
                if previous
                else ProductionContext()
            )
            reader_policy = drafts[-1].reader_policy or prior.reader_policy
            reading = (
                [r for r in work.reviews.reader_list() if r.story_id == story_id] if reader_policy else []
            )
            current_reading = [r for r in reading if r.draft_id == drafts[-1].id]
            needs_reading = reader_policy and (
                not current_reading or current_reading[-1].submission.decision != "ready"
            )
            return queue_role(
                work,
                self.definitions,
                role,
                ProductionContext(
                    story=story,
                    evidence=work.drafts.evidence(story_id),
                    draft=drafts[-1] if drafts else None,
                    reviews=reviews[-1:],
                    review_mode="reconsideration",
                    reader_policy=reader_policy,
                    reader_reviews=reading,
                    phase=("reading-review" if needs_reading else "fact-check") if prior.phase else "",
                    writing_research=prior.writing_research,
                ),
                limits,
            )

    def queue_layout(self, package_id: str, limits: ProductionLimits) -> ProductionTask:
        """排队独立选图；已有待执行任务复用，阻断后显式重排保留原任务并建立后继。"""
        with self.unit() as work:
            source = next((p for p in work.releases.packages() if p.id == package_id), None)
            if source is None:
                raise KeyError("源包不存在")
            require_current_package(work, source)
            context = ProductionContext(
                story=work.stories.get(source.story_id),
                draft=work.drafts.get(source.draft_id),
                evidence=work.drafts.evidence(source.story_id),
                reviews=[r for r in work.reviews.list() if r.id == source.review_id],
                phase="layout-production",
                layout_source_package=source.id,
                reader_policy=work.drafts.get(source.draft_id).reader_policy,
                reader_reviews=[r for r in work.reviews.reader_list() if r.draft_id == source.draft_id],
            )
            for _ in range(20):
                queued = queue_role(work, self.definitions, "publisher", context, limits)
                if queued.status != "blocked":
                    return queued
                context.parent_task_id = queued.id
            raise ValueError("连续阻断过多，请先解决运行环境问题")

    def submit_package(self, task_id: str, token: str, result: ChannelSubmission) -> str:
        """DSH提交选图决策；取图在事务外，最终交接再次检查租约与版本。"""
        from agent4pharma.workflows.production_figures import bound_figure_context, require_inspected_figures
        from agent4pharma.workflows.publication_layout import prepare_article_layout

        with self.unit() as work:
            task, context = bound_figure_context(work, task_id, token)
            require_inspected_figures(task, result.figures)
        if (result.figures or result.emphasis) and result.channel != "local_html":
            raise ValueError("原图排版请选择local_html")
        if context.layout_source_package:
            package = create_article_layout(
                self.unit,
                LayoutRequest(
                    package_id=context.layout_source_package, figures=result.figures, emphasis=result.emphasis
                ),
                self.figure_reader,
                completion=(task_id, token),
                header_reader=self.header_reader,
            )
            return package.id
        prepared = (
            prepare_article_layout(
                context.draft,
                context.evidence,
                result.figures,
                self.figure_reader,
                result.emphasis,
                self.header_reader,
            )
            if result.figures or result.emphasis or (self.header_reader and result.channel == "local_html")
            else None
        )
        return self.handoff.publishing(task_id, token, result, prepared)

    def create_layout(self, request: LayoutRequest) -> ChannelPackage:
        """对当前定稿生成可追溯图文包，不启动模型或对外发布。"""
        return create_article_layout(self.unit, request, self.figure_reader, header_reader=self.header_reader)

    def export_package(self, package_id: str, variant: str = "web") -> tuple[str, str]:
        """读取当前有效包并记本地导出回执；不写任意路径，不对外发送。"""
        with self.unit() as work:
            package = next((p for p in work.releases.packages() if p.id == package_id), None)
            if package is None:
                raise KeyError("渠道包不存在")
            require_current_package(work, package)
            require_reviewable_package(work, package)
            if not any(
                a.package_id == package.id and a.fingerprint == package_fingerprint(package)
                for a in work.releases.approvals()
            ):
                raise ValueError("请先在人工审稿中批准此准确发布包")
            if variant not in {"web", "wechat"}:
                raise ValueError("未知导出格式")
            if variant == "wechat" and package.presentation is None:
                raise ValueError("此包尚无公众号正文")
            work.releases.receipt(
                ReleaseReceipt(
                    id="export-" + package.id, package_id=package.id, status="exported", created_at=utcnow()
                )
            )
            if variant == "wechat":
                return package.presentation.wechat_html, "html"
            return package.body, "html" if package.channel == "local_html" else "md"

    def _revise(self, work: ProductionUnit, story: Story, reason: str, *, status: str) -> Story:
        updated = story.model_copy(
            update={"revision": story.revision + 1, "status": status, "updated_at": utcnow()}
        )
        work.stories.save(updated, story.revision)
        work.tasks.event(uuid.uuid4().hex, story.id, "local-operator", status, reason, utcnow())
        return updated
