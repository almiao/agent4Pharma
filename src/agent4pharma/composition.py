"""Composition root: concrete resources are created here, never on import."""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import Executor
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import JsonValue

from agent4pharma.infrastructure.connectors.factory import connectors
from agent4pharma.infrastructure.connectors.transport import Transport
from agent4pharma.infrastructure.llm.context_visibility import read_visibility_epoch
from agent4pharma.infrastructure.llm.mineru_runtime import mineru_service_home
from agent4pharma.infrastructure.persistence.sqlite.application_storage import SqliteApplicationStorage
from agent4pharma.modules.collection.acquisition_interfaces import SourceAccessPersistence, SourceConnector
from agent4pharma.runtime.settings import ApplicationSettings
from agent4pharma.workflows.daily_literature_discovery import DailyLiteratureDiscovery
from agent4pharma.workflows.evidence_delivery import EvidenceDelivery

if TYPE_CHECKING:
    from agent4pharma.entrypoints.web.paper_continuation_api import PaperContinuationAPI
    from agent4pharma.infrastructure.llm.dependency_control import DependencyController
    from agent4pharma.infrastructure.llm.dependency_probe import DependencyMonitor
    from agent4pharma.infrastructure.persistence.sqlite.discovery_query_cache import DiscoveryQueryCache
    from agent4pharma.infrastructure.persistence.sqlite.intelligence_repository import IntelligenceRepository
    from agent4pharma.infrastructure.persistence.sqlite.maintenance_changes import MaintenanceChangeRepository
    from agent4pharma.infrastructure.persistence.sqlite.prompt_repository import PromptRepository
    from agent4pharma.modules.discovery.intelligence_contracts import IntelligencePolicyVersion
    from agent4pharma.modules.discovery.local_triage import LocalTriage
    from agent4pharma.modules.discovery.paper_selection import PaperSelectionRun
    from agent4pharma.modules.discovery.run_records import DiscoveryRunSnapshot
    from agent4pharma.modules.discovery.screening_policy import ScreeningPolicy
    from agent4pharma.modules.maintenance.agent_tools import MaintenanceAgentTools
    from agent4pharma.modules.maintenance.investigation_tools import InvestigationTools
    from agent4pharma.modules.maintenance.models import InvestigationJob
    from agent4pharma.modules.materials.browser_paper_service import BrowserPaperTools
    from agent4pharma.modules.materials.collection_records import SourceRecordReading
    from agent4pharma.modules.materials.document_reading import BoundDocumentReader
    from agent4pharma.modules.materials.manual_pdf_imports import ManualPdfImports
    from agent4pharma.modules.materials.paper_download_jobs import PaperBrowserControl
    from agent4pharma.modules.operations.article_regression_contracts import ArticleRegressionRepository
    from agent4pharma.modules.sources.collection_plans import CollectionPlan, CollectionPlanCatalog
    from agent4pharma.runtime.production_execution import ProductionTask
    from agent4pharma.runtime.skill_resources import SkillBundle
    from agent4pharma.runtime.workflow_observation import ObservationSink
    from agent4pharma.workflows.background_activity import BackgroundActivitySnapshot
    from agent4pharma.workflows.daily_batch_execution import DailyBatchExecution
    from agent4pharma.workflows.maintenance_workbench import MaintenanceWorkbench
    from agent4pharma.workflows.paper_download_workbench import PaperDownloadWorkbench
    from agent4pharma.workflows.production_workbench import ProductionWorkbench
    from agent4pharma.workflows.wechat_delivery import WechatDeliveryService


def open_application_storage(root: Path) -> SqliteApplicationStorage:
    """创建并打开 SQLite 应用存储，执行所需历史迁移；调用者负责 close。"""
    return SqliteApplicationStorage(root)


def build_bound_document_reader(
    root: Path, download_id: str, *, cache_only: bool = False
) -> BoundDocumentReader:
    """装配独立 DSH 文档读取能力；每次调用核对宿主绑定文件的目录与哈希。"""
    from agent4pharma.infrastructure.llm.mineru_document_reader import MineruDocumentReader, find_mineru
    from agent4pharma.infrastructure.persistence.paper_download_repository import PaperDownloadRepository
    from agent4pharma.modules.materials.document_reading import BoundDocumentReader, VerifiedDocument

    ledger = PaperDownloadRepository(root)

    def document() -> VerifiedDocument:
        result = next((row for row in ledger.results() if row.id == download_id), None)
        if result is None:
            raise ValueError("绑定的原文不存在")
        verified = ledger.verified(result.request)
        if verified is None or verified.id != download_id:
            raise ValueError("绑定原文未校验或文件已变化")
        return VerifiedDocument(Path(verified.file), verified.sha256, verified.pages)

    document()
    return BoundDocumentReader(
        document, MineruDocumentReader(find_mineru(), home=mineru_service_home(root), cache_only=cache_only)
    )


def build_prompt_repository(root: Path) -> PromptRepository:
    """装配节点默认正文；只读发布版本，不初始化模型或覆盖人工配置。"""
    from agent4pharma.infrastructure.persistence.sqlite.prompt_repository import PromptRepository
    from agent4pharma.modules.discovery.news_selection import (
        NEWS_COMPARISON_INSTRUCTIONS,
        NEWS_RANKING_INSTRUCTIONS,
    )
    from agent4pharma.modules.discovery.triage_contracts import TriagePolicy
    from agent4pharma.modules.model_configuration.prompt_contracts import PromptKey
    from agent4pharma.modules.model_configuration.screening_prompts import SHARED_SCREENING_SCOPE
    from agent4pharma.workflows.newsroom_standards import EDITORIAL_STANDARDS

    defaults: dict[PromptKey, str] = {
        "triage": TriagePolicy().instructions,
        "screening_scope": SHARED_SCREENING_SCOPE,
        "news_ranking": NEWS_RANKING_INSTRUCTIONS,
        "news_comparison": NEWS_COMPARISON_INSTRUCTIONS,
    }
    for standard in EDITORIAL_STANDARDS:
        defaults[standard.key] = (
            files("agent4pharma.agents.newsroom").joinpath(standard.resource).read_text(encoding="utf-8")
        )
    for key, role, filename in (
        ("editor", "editor", "system_prompt.md"),
        ("writer", "writer", "system_prompt.md"),
        ("reviewer", "reviewer", "system_prompt.md"),
        ("publisher", "publisher", "system_prompt.md"),
        ("maintenance", "source_maintenance", "system_prompt.md"),
    ):
        defaults[key] = files("agent4pharma.agents." + role).joinpath(filename).read_text(encoding="utf-8")
    return PromptRepository(root, defaults)


def build_observation_repository(root: Path) -> ObservationSink:
    """装配实际步骤与模型工具观测持久化端口。"""
    from agent4pharma.infrastructure.persistence.sqlite.article_regression_repository import (
        ObservationRepository,
    )

    return ObservationRepository(root)


def build_production_workbench(
    root: Path,
    model: str = "deepseek-flash",
    endpoint: str = "https://api.deepseek.com",
) -> ProductionWorkbench:
    """装配四角色制作服务；调用者先升级主库，构造本身不启动模型或外部网络。"""
    from functools import partial

    from agent4pharma.infrastructure.persistence.sqlite.content_research import ContentResearchRepository
    from agent4pharma.infrastructure.persistence.sqlite.intelligence_repository import IntelligenceRepository
    from agent4pharma.infrastructure.persistence.sqlite.production_unit import production_unit
    from agent4pharma.infrastructure.web.content_reader import read_content_source
    from agent4pharma.modules.content.draft_contracts import ResearchEvidence
    from agent4pharma.workflows.production_dependencies import CandidateSnapshot
    from agent4pharma.workflows.production_queue import ProductionRoleDefinition, PublishedRoleDefinitions
    from agent4pharma.workflows.production_workbench import ProductionWorkbench

    def candidates(identities: list[str]) -> list[CandidateSnapshot]:
        store = IntelligenceRepository(root)
        results = []
        for row in store.candidates():
            if row["id"] not in identities:
                continue
            task = store.get(str(row["task_id"]))
            evidence = [
                ResearchEvidence(
                    id=e.id,
                    title=e.title,
                    text=e.text,
                    url=e.url,
                    origin=e.origin,
                    fulltext=e.origin in {"source_fulltext", "primary_fulltext", "primary_fulltext_excerpt"},
                    document=e.document,
                    read_scope=e.read_scope,
                    locators=e.locators,
                )
                for e in [*task.request.evidence, *task.supplemental_evidence]
                if e.status == "available" and e.text.strip()
            ]
            results.append(
                CandidateSnapshot(
                    id=str(row["id"]),
                    assessment_id=task.id,
                    summary=task.result.summary,
                    reason=task.result.attention_reason,
                    evidence=evidence,
                )
            )
        if any(identity.startswith("human-paper-") for identity in identities):
            from agent4pharma.infrastructure.persistence.paper_download_repository import (
                PaperDownloadRepository,
            )
            from agent4pharma.infrastructure.persistence.paper_selection_repository import (
                PaperSelectionRepository,
            )
            from agent4pharma.workflows.imported_paper_recommendation import imported_recommendation_candidate
            from agent4pharma.workflows.selected_paper_download import download_request

            for run in PaperSelectionRepository(root).latest():
                for item in run.items:
                    if item.human_recommendation is None or item.human_recommendation.id not in identities:
                        continue
                    verified = PaperDownloadRepository(root).verified(download_request(run, item))
                    if verified is None:
                        raise ValueError("人工推荐主文校验失效")
                    results.append(
                        imported_recommendation_candidate(run, item.request.evidence[0].id, verified)
                    )
        return results

    definitions = {}
    from agent4pharma.modules.materials.document_reading import (
        DocumentPreparation,
        DocumentReadResult,
        DocumentReference,
    )

    def read_document(
        document: DocumentReference, start: int, end: int, after: str, *, cache_only: bool = False
    ) -> DocumentReadResult:
        bound = build_bound_document_reader(root, document.id, cache_only=cache_only)
        verified = bound.document()
        if verified.sha256 != document.sha256 or verified.pages != document.pages:
            raise ValueError("原文不属于冻结的文档身份")
        return bound.read_document(start, end, after)

    def prepare(document: DocumentReference) -> DocumentPreparation:
        from agent4pharma.infrastructure.llm.document_preparation import prepare_document
        from agent4pharma.infrastructure.llm.mineru_document_reader import find_mineru

        verified = build_bound_document_reader(root, document.id, cache_only=True).document()
        if verified.sha256 != document.sha256 or verified.pages != document.pages:
            raise ValueError("准备原文身份与任务不符")
        return prepare_document(find_mineru(), verified, home=mineru_service_home(root))

    from agent4pharma.modules.materials.document_reading import VerifiedDocument
    from agent4pharma.modules.publishing.article_layout import FigureImage, FigureSelection
    from agent4pharma.modules.publishing.figure_catalog import FigureCatalog

    def figure_document(document: DocumentReference) -> VerifiedDocument:
        verified = build_bound_document_reader(root, document.id, cache_only=True).document()
        if verified.sha256 != document.sha256 or verified.pages != document.pages:
            raise ValueError("原图文档身份与冻结资料不符")
        return verified

    def figure_catalog(document: DocumentReference) -> FigureCatalog:
        from agent4pharma.infrastructure.llm.mineru_document_reader import find_mineru
        from agent4pharma.infrastructure.llm.mineru_figure_catalog import build_figure_catalog

        return build_figure_catalog(
            find_mineru(), figure_document(document), root / "figure-cache", home=mineru_service_home(root)
        )

    def figure_inspector(document: DocumentReference, identity: str) -> FigureImage:
        from agent4pharma.infrastructure.llm.mineru_document_reader import find_mineru
        from agent4pharma.infrastructure.llm.mineru_figure_catalog import inspect_cached_figure

        return inspect_cached_figure(
            find_mineru(),
            figure_document(document),
            identity,
            root / "figure-cache",
            home=mineru_service_home(root),
        )

    from agent4pharma.modules.publishing.paper_header import PaperHeader

    def read_header(document: DocumentReference, title: str) -> PaperHeader:
        from agent4pharma.infrastructure.llm.pdf_paper_header import capture_pdf_header

        try:
            verified = figure_document(document)
        except (OSError, KeyError, ValueError):
            return PaperHeader(status="failed", reason="绑定PDF不可用，已省略题头")
        return capture_pdf_header(verified, title)

    def read_figure(document: DocumentReference, selection: FigureSelection) -> FigureImage:
        return figure_inspector(document, f"p{selection.page}b{selection.block}")

    from agent4pharma.workflows.newsroom_skills import load_newsroom_skills, replace_skill_resources
    from agent4pharma.workflows.newsroom_standards import EDITORIAL_STANDARDS, standard_resource_text

    def published_skills(role: str) -> SkillBundle:
        catalog = build_prompt_repository(root).catalog([s.key for s in EDITORIAL_STANDARDS])
        replacements = {
            s.resource: standard_resource_text(
                next(v for v in catalog[s.key] if v.status in {"published", "builtin"})
            )
            for s in EDITORIAL_STANDARDS
        }
        return replace_skill_resources(load_newsroom_skills(role), replacements)

    for role in ("editor", "writer", "reviewer", "publisher"):
        prompt = files("agent4pharma.agents." + role).joinpath("system_prompt.md").read_text(encoding="utf-8")
        definitions[role] = ProductionRoleDefinition(
            role=role,
            prompt=prompt,
            model=model,
            endpoint=endpoint,
            newsroom_skills=load_newsroom_skills(role),
            newsroom_pipeline=True,
        )
    return ProductionWorkbench(
        partial(production_unit, root),
        candidates,
        PublishedRoleDefinitions(
            definitions, lambda role: build_prompt_repository(root).current(role).text(), published_skills
        ),
        ContentResearchRepository(root).search,
        read_content_source,
        read_unit=partial(production_unit, root, read_only=True),
        figure_reader=read_figure,
        header_reader=read_header,
        figure_catalog=figure_catalog,
        figure_inspector=figure_inspector,
        document_reader=read_document,
        document_preparer=prepare,
        cached_document_reader=lambda d, s, e, a: read_document(d, s, e, a, cache_only=True),
    )


def execute_production_task(root: Path, task_id: str) -> "ProductionTask":
    """按持久化快照执行一个明确授权的角色任务，不暗中运行后续角色。"""
    from agent4pharma.infrastructure.llm.production_runner import DshProductionRunner
    from agent4pharma.workflows.production_dependencies import ProductionContext

    app = build_production_workbench(root)
    task = app.execution.get(task_id)
    optional_fulltext = False
    if task.phase in {"draft-writing", "fact-check"}:
        context = ProductionContext.model_validate_json(task.input_json)
        # 新任务书按需取证，旧任务书仍沿用其冻结的准备门槛。
        optional_fulltext = context.story is not None
    if (
        task.phase in {"draft-writing", "fact-check"}
        and task.newsroom_policy
        and task.status in {"queued", "failed"}
        and not optional_fulltext
    ):
        preparations = app.prepare_documents(task_id)
        if task.phase in {"draft-writing", "fact-check"}:
            import time

            deadline = time.monotonic() + min(task.limits.timeout_seconds, 180)
            while any(item.status != "ready" for item in preparations):
                current = app.execution.get(task_id)
                if current.status in {"paused", "cancelled"}:
                    return current
                failed = next(
                    (item for item in preparations if item.status in {"failed", "unavailable"}), None
                )
                if failed is not None:
                    return app.execution.fail_interrupted(
                        task_id,
                        f"PDF解析准备失败（{failed.error_code or failed.status}），请检查文档准备状态后重试",
                    )
                if time.monotonic() >= deadline:
                    return app.execution.fail_interrupted(
                        task_id, "等待PDF解析缓存超时，请在文档准备状态显示就绪后重试"
                    )
                time.sleep(2)
                preparations = app.prepare_documents(task_id)
    return app.execution.execute(task_id, DshProductionRunner(root, app.execution.is_current))


def production_worker(root: Path, task_id: str, token: str) -> None:
    """内部子进程在有效租约内执行冻结的 DSH 配置。"""
    from agent4pharma.infrastructure.llm.production_runner import run_production_worker

    app = build_production_workbench(root)
    app.context(task_id, token)
    run_production_worker(
        root,
        app.execution.get(task_id),
        lambda evidence: app.register_search_results(task_id, token, evidence),
    )


def collection_providers(
    store: SourceAccessPersistence,
    providers: dict[str, SourceConnector] | None = None,
) -> tuple[Transport | None, dict[str, SourceConnector]]:
    """复用外部连接器，或创建自有传输与连接器；返回的自有传输需由流程关闭。"""
    if providers is not None:
        return None, providers
    transport = Transport(store)
    return transport, connectors(transport)


def build_daily_literature_discovery(
    config: ApplicationSettings,
    store: SqliteApplicationStorage,
    providers: dict[str, SourceConnector] | None = None,
    *,
    configured_only: bool = False,
    source_ids: list[str] | None = None,
    frozen_batch: str | None = None,
) -> DailyLiteratureDiscovery:
    """装配每日发现流程；默认连接真实来源，测试可注入离线连接器。"""
    if frozen_batch:
        return build_frozen_collection_workflow(config, store, frozen_batch)
    if configured_only:
        config.sources = {}
    if config.triage.enabled and not config.triage.policy_json:
        from agent4pharma.modules.discovery.triage_contracts import TriagePolicy

        config.triage.policy_json = TriagePolicy(
            instructions=build_prompt_repository(config.data_dir).current("triage").text()
        ).model_dump_json()
    prepare_discovery_config(config)
    supplied = providers is not None
    transport, providers = collection_providers(store, providers)
    if not supplied:
        from agent4pharma.infrastructure.connectors.rss_feed import FEED_PARSER_VERSION, RssFeedConnector
        from agent4pharma.infrastructure.persistence.sqlite.management_sources import ManagedSourceRepository

        if not configured_only:
            for source in ManagedSourceRepository(config.data_dir).active_feeds():
                providers[source.id] = RssFeedConnector(source.id, source.endpoint_url)
                config.sources[source.id] = {"query": "", "coarse_mode": "semantic_only"}
        from agent4pharma.infrastructure.connectors.configured_web import (
            PARSER_VERSION,
            ConfiguredWebConnector,
        )
        from agent4pharma.infrastructure.web.public_fetch import PublicPageFetcher

        fetcher = PublicPageFetcher(max_bytes=4_000_000)
        plans = build_collection_plan_catalog(config.data_dir).list_plans(enabled_only=True)
        for plan in plans:
            if source_ids and plan.source_id not in source_ids:
                continue
            feed_only = plan.acquisition_mode == "feed_metadata"
            providers[plan.source_id] = (
                RssFeedConnector(
                    plan.source_id, plan.entries[0].url, plan.rss_profile, date_policy=plan.feed_date_policy
                )
                if feed_only
                else ConfiguredWebConnector(plan, fetcher, store)
            )
            config.sources[plan.source_id] = {
                "query": "",
                "coarse_mode": plan.coarse_mode,
                "collection_mode": "configured",
                **({"feed_parser_version": FEED_PARSER_VERSION} if feed_only else {}),
                "connector_version": plan.rss_profile.version
                if feed_only and plan.rss_profile
                else FEED_PARSER_VERSION
                if feed_only
                else PARSER_VERSION,
                "plan_json": plan.model_dump_json(),
            }
        if configured_only and not config.sources:
            if transport:
                transport.close()
            raise ValueError("没有匹配的已启用通用采集配置，请先导入并检查来源状态")
    return DailyLiteratureDiscovery(
        config,
        store,
        providers,
        transport=transport,
        triage=build_local_triage(config) if config.triage.enabled else None,
    )


def build_daily_batch_execution(config: ApplicationSettings) -> DailyBatchExecution:
    """装配显式今日跑批；工作线程拥有数据库及网络资源，不在GET或启动时执行。"""
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    from agent4pharma.infrastructure.persistence.daily_batch_run_repository import DailyBatchRunRepository
    from agent4pharma.modules.discovery.daily_batch_runs import DailyBatchResult
    from agent4pharma.workflows.daily_batch_execution import BatchProgress, DailyBatchExecution

    def run(day: date, progress: BatchProgress) -> DailyBatchResult:
        settings = config.model_copy(deep=True)
        progress("collecting", "")
        store = open_application_storage(settings.data_dir)
        try:
            workflow = build_daily_literature_discovery(settings, store)
            try:
                report = workflow.run(day)
                batch_id = str(report["batch_id"])
                progress("selecting", batch_id)
                selection = screen_batch(workflow.config, batch_id)
                coverage = str(report["coverage_status"])
                return DailyBatchResult(
                    batch_id=batch_id,
                    selection_run_id=selection.id,
                    materials=int(report["unique_materials"]),
                    suggested_downloads=len(selection.comparison_result.selected_ids)
                    if selection.comparison_result
                    else 0,
                    coverage=coverage,
                    partial=coverage != "complete"
                    or bool(report.get("awaiting_triage"))
                    or selection.phase != "fulltexts"
                    or bool(selection.comparison_error)
                    or selection.paused,
                )
            finally:
                workflow.close()
        finally:
            store.close()

    def retry_selection(run_id: str, progress: BatchProgress) -> DailyBatchResult:
        from agent4pharma.infrastructure.persistence.paper_selection_repository import (
            PaperSelectionRepository,
        )

        run = PaperSelectionRepository(config.data_dir).get(run_id)
        if run.paused:
            raise ValueError("本轮已暂停，请先处理暂停状态")
        progress("selecting", run.batch_id)
        selection = screen_batch(config, run.batch_id, run_id=run.id, retry_models=True)
        store = open_application_storage(config.data_dir)
        try:
            report = store.report(run.batch_id)
        finally:
            store.close()
        return DailyBatchResult(
            batch_id=run.batch_id,
            selection_run_id=selection.id,
            materials=int(report["unique_materials"]),
            suggested_downloads=len(selection.comparison_result.selected_ids)
            if selection.comparison_result
            else 0,
            coverage=str(report["coverage_status"]),
            partial=report["coverage_status"] != "complete" or selection.phase != "fulltexts",
        )

    return DailyBatchExecution(
        DailyBatchRunRepository(config.data_dir),
        lambda: datetime.now(ZoneInfo(config.timezone)).date(),
        run,
        retry_operation=retry_selection,
    )


def build_collection_plan_catalog(root: Path) -> CollectionPlanCatalog:
    """装配来源配置专用仓储，不扩展旧集中存储门面。"""
    from agent4pharma.infrastructure.persistence.sqlite.collection_plan_repository import (
        CollectionPlanRepository,
    )

    return CollectionPlanRepository(root)


def save_collection_plan(definition: str, root: Path, *, expected_hash: str | None = None) -> CollectionPlan:
    """校验请求和 CSS 后直接保存数据库计划；更新执行乐观锁，不读取站点配置文件。"""
    from agent4pharma.infrastructure.web.configured_extraction import validate_selectors
    from agent4pharma.modules.sources.collection_plans import CollectionPlan

    plan = CollectionPlan.model_validate_json(definition)
    validate_selectors(plan)
    catalog = build_collection_plan_catalog(root)
    return catalog.save(plan, expected_hash=expected_hash)


def build_frozen_collection_workflow(
    config: ApplicationSettings,
    store: SqliteApplicationStorage,
    batch_id: str,
) -> DailyLiteratureDiscovery:
    """按旧批次内冻结的通用配置重建采集流程，当前目录修改不替换旧队列规则。"""
    from agent4pharma.infrastructure.connectors.configured_web import PARSER_VERSION, ConfiguredWebConnector
    from agent4pharma.infrastructure.connectors.rss_feed import FEED_PARSER_VERSION, RssFeedConnector
    from agent4pharma.infrastructure.web.public_fetch import PublicPageFetcher
    from agent4pharma.modules.sources.collection_plans import CollectionPlan

    snapshot = store.job(batch_id)["request"]["policy"]
    frozen = ApplicationSettings.model_validate(
        {**snapshot, "data_dir": config.data_dir, "llm": config.llm, "page_budget": config.page_budget}
    )
    frozen.freeze_snapshot(snapshot)
    if not frozen.sources or any("plan_json" not in options for options in frozen.sources.values()):
        raise ValueError("此入口仅续跑全部由数据库配置驱动的媒体批次")
    fetcher = PublicPageFetcher(max_bytes=4_000_000)
    providers = {}
    for source, options in frozen.sources.items():
        plan = CollectionPlan.model_validate_json(options["plan_json"])
        feed_only = plan.acquisition_mode == "feed_metadata"
        expected_version = (
            plan.rss_profile.version
            if feed_only and plan.rss_profile
            else (FEED_PARSER_VERSION if feed_only else PARSER_VERSION)
        )
        if options.get("connector_version") != expected_version or (
            feed_only and options.get("feed_parser_version") != FEED_PARSER_VERSION
        ):
            raise ValueError("历史批次提取器版本不匹配，不能静默换用当前实现续跑")
        providers[source] = (
            RssFeedConnector(source, plan.entries[0].url, plan.rss_profile, date_policy=plan.feed_date_policy)
            if feed_only
            else ConfiguredWebConnector(plan, fetcher, store)
        )
    return DailyLiteratureDiscovery(
        frozen,
        store,
        providers,
        triage=build_local_triage(frozen) if frozen.triage.enabled else None,
    )


def build_local_triage(config: ApplicationSettings) -> LocalTriage:
    """按冻结配置装配本地预选，不在构造时联网或加载 GPU。"""
    from agent4pharma.infrastructure.llm.local_decider import LocalDecider
    from agent4pharma.infrastructure.persistence.sqlite.rss_triage_repository import RssTriageRepository
    from agent4pharma.modules.discovery.local_triage import LocalTriage
    from agent4pharma.modules.discovery.triage_contracts import TriagePolicy

    return LocalTriage(
        RssTriageRepository(config.data_dir),
        LocalDecider(config.triage.endpoint, config.triage.model_revision, config.triage.timeout_seconds),
        config.triage.model_revision,
        TriagePolicy.model_validate_json(config.triage.policy_json)
        if config.triage.policy_json
        else TriagePolicy(instructions=build_prompt_repository(config.data_dir).current("triage").text()),
    )


def build_discovery_query_cache(root: Path) -> "DiscoveryQueryCache":
    """装配只读批次投影缓存；不打开连接，资源由HTTP服务关闭。"""
    from agent4pharma.infrastructure.persistence.sqlite.discovery_query_cache import DiscoveryQueryCache

    return DiscoveryQueryCache(root, lambda batch: discovery_run_view(root, batch))


def discovery_run_view(root: Path, batch_id: str = "") -> "DiscoveryRunSnapshot":
    """装配批次运行只读投影，不执行采集、模型或业务状态迁移。"""
    from agent4pharma.infrastructure.persistence.sqlite.discovery_run_view import DiscoveryRunReader
    from agent4pharma.infrastructure.persistence.sqlite.management_sources import ManagedSourceRepository
    from agent4pharma.modules.sources.source_categories import source_category

    snapshot = DiscoveryRunReader(root).snapshot(batch_id)
    plans = {p.source_id: p for p in build_collection_plan_catalog(root).list_plans()}
    directory = {s.id: s for s in ManagedSourceRepository(root).list_sources(limit=200)}
    articles_by_source = {}
    for article in snapshot.articles:
        articles_by_source.setdefault(article.source_id, []).append(article)
    for source in snapshot.sources:
        plan = plans.get(source.source_id)
        registered = directory.get(source.source_id)
        source.collection_availability = registered.collection_availability if registered else None
        source.name = registered.name if registered else plan.name if plan else source.source_id
        if not source.request_url:
            source.request_url = (
                plan.entries[0].url if plan else registered.endpoint_url if registered else ""
            )
            source.request_url_basis = "current_directory" if source.request_url else "unknown"
        related_articles = articles_by_source.get(source.source_id, [])
        categories = {a.category for a in related_articles} - {"unknown"}
        source.category = (
            next(iter(categories))
            if len(categories) == 1
            else source_category(
                source.source_id,
                source_kind=registered.source_kind if registered else plan.source_kind if plan else "",
                material_type=plan.material_type if plan and not registered else "",
                url=registered.homepage_url if registered else plan.homepage_url if plan else "",
            )
        )
        for article in related_articles:
            if article.category == "unknown":
                article.category = source.category
    return snapshot


def discovery_article_reading(
    root: Path, batch_id: str, record_id: str, offset: int = 0
) -> SourceRecordReading:
    """装配有批次成员校验的归档阅读，不发起网络或模型调用。"""
    from agent4pharma.infrastructure.persistence.sqlite.discovery_run_view import DiscoveryRunReader

    return DiscoveryRunReader(root).article_reading(batch_id, record_id, offset)


def rss_triage_view(
    root: Path, batch_id: str = "", *, view: str = "", run_id: str = ""
) -> dict[str, JsonValue]:
    """管理查询读取来源规则及本地预选产物，不发起模型、采集或配置修改。"""
    from agent4pharma.infrastructure.persistence.chaoslib_access import ChaosLibAccess
    from agent4pharma.infrastructure.persistence.paper_download_repository import PaperDownloadRepository
    from agent4pharma.infrastructure.persistence.sqlite.rss_triage_repository import RssTriageRepository

    repository = RssTriageRepository(root)
    if view not in {"", "triage", "rank", "ingest"}:
        raise ValueError("未知预选视图")
    batches = repository.batches(batch_id)
    selected = batch_id or (batches[0]["batch_id"] if batches else "")
    if selected and selected not in {batch["batch_id"] for batch in batches}:
        raise KeyError("本地预选批次不存在")
    policy = repository.policy_snapshot(selected) if selected else None
    downloads = PaperDownloadRepository(root).results(selected) if selected and not view else []
    selections = []
    run_summaries = []
    if selected and not view:
        selections = paper_selection_status(root, selected)
    elif selected and view == "rank":
        from agent4pharma.infrastructure.persistence.paper_selection_repository import (
            PaperSelectionRepository,
        )
        from agent4pharma.infrastructure.persistence.sqlite.intelligence_repository import (
            IntelligenceRepository,
        )
        from agent4pharma.modules.discovery.paper_selection_view import selection_view

        runs = PaperSelectionRepository(root)
        summaries = runs.summaries(selected)
        run_summaries = [
            {"id": item.id, "batch_id": item.batch_id, "created_at": item.created_at, "phase": item.phase}
            for item in summaries
        ]
        if run_id and run_id not in {item.id for item in summaries}:
            raise KeyError("排序运行不属于当前批次")
        if summaries:
            run = runs.get(run_id or summaries[-1].id)
            tasks = (
                IntelligenceRepository(root).tasks_for_selection(run.id) if run.phase == "abstracts" else ()
            )
            selections = [selection_view(run, tasks)]
    return {
        "batches": batches,
        "selected": selected,
        "policy": policy.model_dump(mode="json") if policy else None,
        "downloads": [row.model_dump(mode="json") for row in downloads],
        "paper_selections": selections,
        "runs": run_summaries,
        "download_account": ChaosLibAccess(root / "private" / "chaoslib").status(),
        "decisions": [row.model_dump(mode="json") for row in repository.decisions(selected)]
        if selected and view in {"", "triage"}
        else [],
        "sources": [
            plan.model_dump(mode="json")
            for plan in build_collection_plan_catalog(root).list_plans()
            if plan.rss_profile
        ]
        if not view
        else [],
    }


def paper_selection_page(root: Path, batch_id: str = "", run_id: str = "") -> dict[str, JsonValue]:
    """读取精选页所需的单个运行投影；不加载来源文章、历史全池、候选或制作快照。"""
    from agent4pharma.infrastructure.persistence.paper_selection_repository import PaperSelectionRepository
    from agent4pharma.infrastructure.persistence.sqlite.intelligence_repository import IntelligenceRepository
    from agent4pharma.infrastructure.persistence.sqlite.rss_triage_repository import RssTriageRepository
    from agent4pharma.workflows.paper_selection_page import selection_page

    batches = RssTriageRepository(root).batches(batch_id)
    identities = [batch["batch_id"] for batch in batches]
    selected = batch_id or (identities[0] if identities else "")
    if selected and selected not in identities:
        raise KeyError("本地预选批次不存在")
    result = selection_page(
        PaperSelectionRepository(root),
        selected,
        run_id,
        task_reader=lambda identity: IntelligenceRepository(root).tasks_for_selection(identity),
    )
    result["batches"] = identities
    return result


def paper_selection_downloads(root: Path, batch_id: str) -> dict[str, JsonValue]:
    """按需读取指定批次下载历史；不附带筛选运行、全部论文或账户诊断。"""
    from agent4pharma.infrastructure.persistence.paper_download_repository import PaperDownloadRepository
    from agent4pharma.infrastructure.persistence.sqlite.rss_triage_repository import RssTriageRepository

    if batch_id not in {batch["batch_id"] for batch in RssTriageRepository(root).batches(batch_id)}:
        raise KeyError("本地预选批次不存在")
    return {
        "downloads": [
            item.model_dump(mode="json") for item in PaperDownloadRepository(root).results(batch_id)
        ]
    }


def build_investigation_tools(root: Path, job_id: str) -> "InvestigationTools":
    """为绑定调查装配公开页面读取、证据及精确查重能力，不初始化模型。"""
    from agent4pharma.infrastructure.persistence.sqlite.investigation_evidence import EvidenceRepository
    from agent4pharma.infrastructure.persistence.sqlite.investigations import InvestigationRepository
    from agent4pharma.infrastructure.persistence.sqlite.management_sources import ManagedSourceRepository
    from agent4pharma.infrastructure.persistence.sqlite.material_lookup import LocalMaterialLookup
    from agent4pharma.infrastructure.web.article_parser import parse_document
    from agent4pharma.infrastructure.web.public_fetch import PublicPageFetcher
    from agent4pharma.modules.maintenance.investigation_tools import InvestigationTools

    return InvestigationTools(
        job_id,
        InvestigationRepository(root),
        EvidenceRepository(root),
        LocalMaterialLookup(root),
        ManagedSourceRepository(root),
        PublicPageFetcher(),
        parse_document,
    )


def build_maintenance_workbench(
    config_path: Path, config: ApplicationSettings, *, recover: bool = True
) -> "MaintenanceWorkbench":
    """初始化工作台仓储与草案，恢复中断状态，保留旧每日发现流程。"""
    from agent4pharma.infrastructure.llm.investigation_runner import DshInvestigationRunner
    from agent4pharma.infrastructure.persistence.sqlite.investigations import InvestigationRepository
    from agent4pharma.infrastructure.persistence.sqlite.management_policies import PolicyRepository
    from agent4pharma.infrastructure.persistence.sqlite.management_sources import ManagedSourceRepository
    from agent4pharma.modules.discovery.screening_policy import ScreeningPolicy
    from agent4pharma.workflows.maintenance_workbench import MaintenanceWorkbench

    root = config.data_dir.resolve()
    store = open_application_storage(root)
    try:
        store.seed_sources(config)
    finally:
        store.close()
    policies, cases = PolicyRepository(root), InvestigationRepository(root)
    policies.ensure_default(ScreeningPolicy())
    if recover:
        cases.recover_interrupted()
    return MaintenanceWorkbench(
        ManagedSourceRepository(root),
        policies,
        cases,
        lambda job_id: build_investigation_tools(root, job_id),
        DshInvestigationRunner(config_path, config),
        config.llm.model,
        lambda: bool(os.environ.get("DEEPSEEK_API_KEY")),
        hashlib.sha256(
            files("agent4pharma.agents.source_maintenance").joinpath("system_prompt.md").read_bytes()
        ).hexdigest(),
        intelligence_store=build_intelligence_repository(root),
        model_settings=config.llm,
        changes=build_change_repository(root),
        prompt_source=lambda: build_prompt_repository(root).current("maintenance").text(),
    )


def render_workbench_queries(policy: "ScreeningPolicy") -> dict[str, str]:
    """为界面装配具体来源查询渲染，不发送请求或改写采集配置。"""
    from agent4pharma.infrastructure.connectors.screening_queries import render_queries

    return render_queries(policy)


def build_intelligence_repository(root: Path) -> IntelligenceRepository:
    """装配短连接情报仓储，不初始化 SDK。"""
    from agent4pharma.infrastructure.persistence.sqlite.intelligence_repository import IntelligenceRepository

    return IntelligenceRepository(root)


def prepare_discovery_config(config: ApplicationSettings) -> None:
    """绑定已发布粗筛/精筛版本并编译来源查询，使新运行指纹覆盖全部判断配置。"""
    from agent4pharma.infrastructure.persistence.sqlite.management_policies import PolicyRepository
    from agent4pharma.modules.discovery.screening_policy import ScreeningPolicy

    repository = PolicyRepository(config.data_dir)
    repository.ensure_default(ScreeningPolicy())
    if not config.screening_json:
        config.screening_json = repository.get_policy().definition.model_dump_json()
    if not config.intelligence_policy_ref:
        config.intelligence_policy_ref = build_selection_policy(config.data_dir).id
    config.intelligence_model = config.llm.model
    config.intelligence_endpoint = config.llm.base_url
    config.intelligence_max_tokens = config.llm.max_tokens
    queries = render_workbench_queries(ScreeningPolicy.model_validate_json(config.screening_json))
    for name, settings in config.sources.items():
        if settings.get("query_mode") == "policy" and name in queries:
            settings["query"] = queries[name]


def screen_batch(
    config: ApplicationSettings,
    batch_id: str,
    *,
    abstract_budget: int | None = None,
    download_budget: int = 0,
    retry_downloads: bool = False,
    retry_models: bool = False,
    run_id: str = "",
) -> PaperSelectionRun:
    """本地摘要、排序前十、商业复核五篇；全文只接受人工下载，不回退全池商业筛选。"""
    if not config.triage.enabled:
        raise ValueError("摘要筛选需要启用本地 Decider；不回退商业模型")
    if download_budget or retry_downloads:
        raise ValueError("全文已改为人工下载，请在运行台查看精选名单")
    return screen_local_batch(
        config,
        batch_id,
        abstract_budget=500 if abstract_budget is None else abstract_budget,
        retry_models=retry_models,
        run_id=run_id,
    )


def build_change_repository(root: Path) -> MaintenanceChangeRepository:
    """人工建议确认的持久化工作单元，不向模型暴露 resolve。"""
    from agent4pharma.infrastructure.persistence.sqlite.maintenance_changes import MaintenanceChangeRepository

    return MaintenanceChangeRepository(root)


def download_papers_with_chaoslib(project_root: Path, dois: list[str], out_dir: Path) -> list[dict]:
    """显式单篇命令最多两项，复用同一账号预算和逐次账本；不再绕过限流直接调用连接器。"""
    from agent4pharma.infrastructure.persistence.paper_download_repository import PaperDownloadRepository
    from agent4pharma.infrastructure.web.chaoslib_downloader import ChaosLibDownloader
    from agent4pharma.modules.materials.paper_downloads import PaperDownloadRequest

    if not 1 <= len(dois) <= 2:
        raise ValueError("单轮仅允许 1–2 篇，禁止批量回填")
    root = project_root.resolve() / "data"
    if out_dir.resolve() != (root / "paper-downloads" / "chaoslib").resolve():
        raise ValueError("下载统一保存在项目 data/paper-downloads/chaoslib，以保留身份及哈希校验")
    ledger = PaperDownloadRepository(root)
    downloader = ChaosLibDownloader(root, project_root)
    results = []
    for doi in dois:
        request = PaperDownloadRequest(
            batch_id="manual-chaoslib",
            decision_id="manual:" + doi,
            material_ref="doi:" + doi,
            source_id="manual",
            source_record_id="doi:" + doi,
            title="",
            journal="",
            url="https://doi.org/" + doi,
            identifier=doi,
        )
        result = downloader.download(request)
        ledger.save(result)
        results.append(result.model_dump(mode="json"))
    return results


def login_chaoslib(project_root: Path, *, human: bool = False) -> dict[str, str]:
    """登录和人工验证使用同一持久浏览器会话；不输出凭证、Cookie 或令牌。"""
    from agent4pharma.infrastructure.persistence.chaoslib_access import ChaosLibAccess
    from agent4pharma.infrastructure.web.chaoslib import load_settings, login_with_browser, save_session
    from agent4pharma.runtime.locking import worker_lock

    settings = load_settings(project_root)
    with worker_lock(settings.state_dir):
        access = ChaosLibAccess(settings.state_dir)
        reason = access.authentication(human=human)
        if reason:
            return {"status": "cooldown", "reason": reason}
        session = login_with_browser(settings, headless=not human)
        save_session(settings, session)
        access.authenticated()
    return {"status": "authenticated", "expires_at": session.expires_at.isoformat()}


def confirm_chaoslib_restored(project_root: Path) -> None:
    """人工确认恢复后仅解除本地暂停，保留每日预算；不会证明账号或下载实际恢复。"""
    from agent4pharma.infrastructure.persistence.chaoslib_access import ChaosLibAccess
    from agent4pharma.infrastructure.web.chaoslib import load_settings
    from agent4pharma.runtime.locking import worker_lock

    settings = load_settings(project_root)
    with worker_lock(settings.state_dir):
        ChaosLibAccess(settings.state_dir).confirm_restored()


def paper_selection_status(root: Path, batch_id: str = "") -> list[dict[str, JsonValue]]:
    """只读摘要清单和人工待办，不初始化模型、凭证、下载或新运行。"""
    from agent4pharma.infrastructure.persistence.paper_selection_repository import PaperSelectionRepository
    from agent4pharma.infrastructure.persistence.sqlite.intelligence_repository import IntelligenceRepository
    from agent4pharma.modules.discovery.paper_selection_view import selection_view

    tasks = IntelligenceRepository(root)
    return [
        selection_view(run, tasks.tasks_for_selection(run.id) if run.phase == "abstracts" else ())
        for run in PaperSelectionRepository(root).latest(batch_id)
    ]


def build_paper_download_workbench(
    root: Path, *, debug: bool = False, browser_connection: PaperBrowserControl | None = None
) -> PaperDownloadWorkbench:
    """装配人工单篇 PDF 工作台；锁与校验复用正式清单和下载账本，不启动模型。"""
    from agent4pharma.infrastructure.persistence.chaoslib_access import ChaosLibAccess
    from agent4pharma.infrastructure.persistence.paper_download_job_repository import (
        PaperDownloadJobRepository,
    )
    from agent4pharma.infrastructure.persistence.paper_download_repository import PaperDownloadRepository
    from agent4pharma.infrastructure.persistence.paper_selection_repository import PaperSelectionRepository
    from agent4pharma.infrastructure.web.daily_browser_connection import DailyBrowserConnection
    from agent4pharma.infrastructure.web.daily_browser_downloader import DailyBrowserDownloader
    from agent4pharma.modules.discovery.paper_selection import (
        record_paper_download,
        require_pdf_association,
        require_shortlisted_paper,
    )
    from agent4pharma.modules.materials.paper_download_jobs import PaperDownloadObserver
    from agent4pharma.modules.materials.paper_downloads import PaperDownloadRequest, PaperDownloadResult
    from agent4pharma.runtime.locking import worker_lock
    from agent4pharma.workflows.paper_download_workbench import PaperDownloadWorkbench
    from agent4pharma.workflows.selected_paper_download import download_request

    selections, ledger = PaperSelectionRepository(root), PaperDownloadRepository(root)
    browser = browser_connection or DailyBrowserConnection(root)

    def binding(run_id: str, record_id: str) -> PaperDownloadRequest:
        run = selections.get(run_id)
        if debug:
            item = next((item for item in run.items if item.request.evidence[0].id == record_id), None)
            if item is None:
                raise ValueError("调试论文必须来自当前已登记清单")
            request = download_request(run, item)
            return request.model_copy(update={"decision_id": "debug:" + request.decision_id})
        item = require_shortlisted_paper(run, record_id)
        return download_request(run, item)

    def acquire(
        run_id: str, record_id: str, restore: bool, observer: PaperDownloadObserver
    ) -> PaperDownloadResult:
        with worker_lock(root / "intelligence"):
            request = binding(run_id, record_id)
            cached = next(
                (
                    r
                    for r in ledger.results(request.batch_id)
                    if r.request.decision_id == request.decision_id and r.status == "downloaded"
                ),
                None,
            )
            if cached:
                ready_pdf(run_id, record_id)
                result = cached
            else:
                if restore:
                    with worker_lock(root / "private/chaoslib"):
                        ChaosLibAccess(root / "private/chaoslib").confirm_restored()
                result = DailyBrowserDownloader(root, observer, connection=browser).download(request)
                ledger.save(result)
            if not debug:
                run = selections.get(run_id)
                record_paper_download(run, record_id, result)
                selections.save(run)
            return result

    def ready_pdf(run_id: str, record_id: str) -> bytes:
        # 查看人工关联主文不扩大自动获取权限；acquire 仍使用原清单守卫。
        run = selections.get(run_id)
        request = (
            binding(run_id, record_id)
            if debug
            else download_request(run, require_pdf_association(run, record_id))
        )
        result = next(
            (
                r
                for r in ledger.results(request.batch_id)
                if r.request.decision_id == request.decision_id and r.status == "downloaded"
            ),
            None,
        )
        if not result or not result.identity_match or not result.sha256:
            raise ValueError("尚未取得通过身份校验的 PDF")
        path = Path(result.file).resolve(strict=True)
        if not path.is_relative_to((root / "paper-downloads").resolve()):
            raise ValueError("PDF 不属于项目下载目录")
        body = path.read_bytes()
        if not body.startswith(b"%PDF") or hashlib.sha256(body).hexdigest() != result.sha256:
            raise ValueError("PDF 已改变，需重新校验后下载")
        return body

    return PaperDownloadWorkbench(
        PaperDownloadJobRepository(root),
        binding,
        acquire,
        ready_pdf,
        lambda: worker_lock(root / "private/pdf-jobs"),
        browser=browser,
    )


def build_browser_paper_tools(root: Path, run_id: str, record_id: str) -> BrowserPaperTools:
    """绑定已冻结个位数清单；未通过摘要或清单外资料不能连接浏览器。"""
    from agent4pharma.infrastructure.persistence.paper_download_repository import PaperDownloadRepository
    from agent4pharma.infrastructure.persistence.paper_selection_repository import PaperSelectionRepository
    from agent4pharma.infrastructure.web.daily_browser_downloader import DailyBrowserDownloader
    from agent4pharma.modules.materials.browser_paper_service import BrowserPaperTools
    from agent4pharma.workflows.selected_paper_download import download_request

    run = PaperSelectionRepository(root).get(run_id)
    item = next((i for i in run.items if i.request.evidence[0].id == record_id), None)
    if (
        run.phase != "fulltexts"
        or item is None
        or item.rank is None
        or not 1 <= item.rank <= run.shortlist_limit
        or item.state == "complete"
    ):
        raise ValueError("论文尚未通过摘要筛选或不属于个位数清单")
    return BrowserPaperTools(
        download_request(run, item), DailyBrowserDownloader(root), PaperDownloadRepository(root)
    )


def install_daily_browser_runtime(root: Path) -> dict[str, str]:
    """显式准备官方运行库；完成后仍需用户安装扩展和确认浏览器连接。"""
    from agent4pharma.infrastructure.web.daily_browser_runtime import (
        EXTENSION_URL,
        MCP_VERSION,
        DailyBrowserRuntime,
    )

    DailyBrowserRuntime(root).install()
    return {"status": "runtime_installed", "version": MCP_VERSION, "extension_url": EXTENSION_URL}


def inspect_daily_browser(root: Path) -> dict[str, str]:
    """仅检查用户所选 Chaoslib 页；不读取其他页、不登录、不请求论文和解除暂停。"""
    import asyncio

    from agent4pharma.infrastructure.web.daily_browser_downloader import DailyBrowserDownloader
    from agent4pharma.infrastructure.web.daily_browser_runtime import DailyBrowserError
    from agent4pharma.runtime.locking import worker_lock

    try:
        with worker_lock(root / "private/chaoslib"):
            status = asyncio.run(DailyBrowserDownloader(root).inspect())
    except Exception as exc:  # CLI 任务边界，底层扩展错误可能携带私有配对 URL。
        status = str(exc) if isinstance(exc, DailyBrowserError) else "browser_connection_requires_user"
    return {"status": status, "download_attempts": "0"}


def create_browser_paper_patch(config_path: Path, run_id: str, record_id: str) -> Path:
    """为 DSH 生成可审阅的绑定论文获取配置，不安装扩展、不执行模型或下载。"""
    from agent4pharma.infrastructure.llm.patch import create_patch

    config = ApplicationSettings.load(config_path)
    build_browser_paper_tools(config.data_dir, run_id, record_id)
    return create_patch(
        config_path,
        config,
        "browser-" + hashlib.sha256((run_id + record_id).encode()).hexdigest()[:24],
        files("agent4pharma.agents.intelligence")
        .joinpath("browser_acquisition.md")
        .read_text(encoding="utf-8"),
        mcp_args=[
            "-m",
            "agent4pharma",
            "browser-paper-mcp",
            "--root",
            str(config.data_dir.resolve()),
            "--run",
            run_id,
            "--record",
            record_id,
        ],
        tool_timeout_ms=240000,
    )


def paper_selection_automation_finished(root: Path, batch_id: str) -> bool:
    """只表示本轮自动工作已结束；人工下载待办与容量延期保留，不让调度持续重试。"""
    from agent4pharma.infrastructure.persistence.paper_selection_repository import PaperSelectionRepository

    runs = PaperSelectionRepository(root).latest(batch_id)
    if not runs:
        return False
    if runs[-1].selection_method == "local_decider_v1":
        return (
            runs[-1].paused
            or runs[-1].phase == "fulltexts"
            or any(i.state in {"local_failed", "review_failed"} for i in runs[-1].items)
        )
    return not any(
        i.state in {"awaiting_abstract", "shortlisted"}
        or (i.state == "abstract_failed" and i.abstract_attempts < 2)
        or (i.state == "fulltext_failed" and i.fulltext_attempts < 2)
        for i in runs[-1].items
    )


def import_selected_paper(root: Path, run_id: str, record_id: str, path: Path) -> dict[str, JsonValue]:
    """人工 PDF 只关联本轮登记论文；复制后校验主文身份，不改变模型排名或执行模型。"""
    import shutil
    import uuid

    from agent4pharma.infrastructure.persistence.paper_download_repository import PaperDownloadRepository
    from agent4pharma.infrastructure.persistence.paper_selection_repository import PaperSelectionRepository
    from agent4pharma.infrastructure.web.paper_pdf_validation import validate_pdf
    from agent4pharma.modules.discovery.paper_selection import require_pdf_association
    from agent4pharma.modules.materials.paper_downloads import PaperDownloadResult
    from agent4pharma.runtime.locking import worker_lock
    from agent4pharma.shared.clock import utcnow
    from agent4pharma.workflows.selected_paper_download import download_request

    with worker_lock(root / "intelligence"):
        store = PaperSelectionRepository(root)
        run = store.get(run_id)
        item = require_pdf_association(run, record_id)
        if run.paused or item.state == "complete":
            raise ValueError("只能为未暂停且尚未完成的论文导入主文")
        request = download_request(run, item)
        cached = PaperDownloadRepository(root).verified(request)
        if cached is not None:
            return cached.model_dump(mode="json")
        source = path.resolve(strict=True)
        with source.open("rb") as stream:
            if stream.read(4) != b"%PDF":
                raise ValueError("导入文件不是 PDF")
        attempt_id = uuid.uuid4().hex
        target = root.resolve() / "paper-downloads" / "manual" / (attempt_id + ".pdf")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        request = download_request(run, item)
        result = PaperDownloadResult.model_validate(
            {
                **validate_pdf(target, request.identifier, request.title),
                "id": attempt_id,
                "request": request,
                "channel": "manual",
                "runtime_profile": {"original_filename": source.name},
                "created_at": utcnow(),
            }
        )
        PaperDownloadRepository(root).save(result)
        item.download_id = result.id
        if result.status == "downloaded":
            item.state, item.acquisition_reason = "shortlisted", "人工主文已校验"
        else:
            item.state, item.acquisition_reason = "manual_required", "导入文件未通过主文身份或格式校验"
        store.save(run)
        return result.model_dump(mode="json")


def chaoslib_routes() -> list[dict]:
    """返回已验证入口与已知阻断，供命令行展示能力边界。"""
    from agent4pharma.infrastructure.web.chaoslib import BLOCKED_PREFIXES, ROUTES

    rows = [
        {"prefix": route.doi_prefix, "publisher": route.name, "menu_id": route.menu_id, "status": "route"}
        for route in ROUTES
    ]
    rows.extend(
        {"prefix": prefix, "publisher": "", "menu_id": 0, "status": "blocked", "reason": reason}
        for prefix, reason in BLOCKED_PREFIXES.items()
    )
    return rows


def build_dependency_monitor(decider_endpoint: str = "", root: Path | None = None) -> DependencyMonitor:
    """装配只读依赖检查器，初始化不探测服务、不读取凭证。"""
    from agent4pharma.infrastructure.llm.dependency_probe import DependencyMonitor
    from agent4pharma.infrastructure.llm.mineru_document_reader import find_mineru

    return DependencyMonitor(
        find_mineru(), decider_endpoint, mineru_service_home(root) if root is not None else None
    )


def build_dependency_controller(root: Path, monitor: DependencyMonitor) -> DependencyController:
    """为运行台装配显式本机依赖控制，复用同一健康检查与缓存。"""
    from agent4pharma.infrastructure.llm.dependency_control import DependencyController

    return DependencyController(root, monitor)


def background_activity_status(root: Path, jobs: list[InvestigationJob]) -> BackgroundActivitySnapshot:
    """汇总本机业务任务，只读取公开仓储；GET 不恢复、重试或启动任何工作。"""
    from datetime import UTC, datetime, timedelta

    from agent4pharma.infrastructure.persistence.daily_batch_run_repository import DailyBatchRunRepository
    from agent4pharma.infrastructure.persistence.paper_download_job_repository import (
        PaperDownloadJobRepository,
    )
    from agent4pharma.infrastructure.persistence.paper_selection_repository import PaperSelectionRepository
    from agent4pharma.infrastructure.persistence.sqlite.intelligence_repository import IntelligenceRepository
    from agent4pharma.infrastructure.persistence.sqlite.production_unit import production_unit
    from agent4pharma.workflows.background_activity import (
        activity_snapshot,
        daily_batch_activity,
        download_activity,
        intelligence_activity,
        investigation_activity,
        paper_continuation_activity,
        production_activity,
        selection_activity,
    )

    now = datetime.now(UTC)
    intelligence = IntelligenceRepository(root)
    summaries = intelligence.activity_summaries((now - timedelta(days=1)).isoformat())
    selections = PaperSelectionRepository(root)
    summaries_by_run = selections.summaries()
    identities = {run.id for run in summaries_by_run}
    latest_by_batch = {run.batch_id: run for run in summaries_by_run}
    latest_runs = {batch: selections.get(summary.id) for batch, summary in latest_by_batch.items()}
    daily = DailyBatchRunRepository(root).all()
    active_batches = {run.batch_id for run in daily if run.status in {"collecting", "selecting"}}
    represented = {run.result.selection_run_id for run in daily if run.result}
    items = [
        selection_activity(
            run, intelligence.tasks_for_selection(run.id) if run.phase == "abstracts" else (), summaries
        )
        for run in latest_runs.values()
        if run.batch_id not in active_batches and run.id not in represented
    ]
    items.extend(daily_batch_activity(run) for run in daily)
    items.extend(
        intelligence_activity(task)
        for task in summaries
        if not (task.stage == "abstract" and task.selection_ref in identities)
    )
    items.extend(download_activity(job) for job in PaperDownloadJobRepository(root).all())
    with production_unit(root, read_only=True) as work:
        production_tasks = work.tasks.list()
    active_task_ids = {task.id for task in production_tasks if task.status in {"queued", "running"}}
    items.extend(
        activity
        for run in latest_runs.values()
        for activity in paper_continuation_activity(run, active_task_ids)
    )
    predecessors = {task.parent_task_id for task in production_tasks if task.parent_task_id}
    items.extend(
        production_activity(task, has_successor=task.id in predecessors)
        for task in production_tasks
        if task.status != "cancelled"
    )
    items.extend(investigation_activity(job) for job in jobs)
    return activity_snapshot(items, now)


def screen_local_batch(
    config: ApplicationSettings,
    batch_id: str,
    *,
    abstract_budget: int = 500,
    retry_models: bool = False,
    run_id: str = "",
    review_budget: int = 10,
) -> PaperSelectionRun:
    """按本地摘要＋原生排序迁入独立运行，商业复核限前十；不自动下载全文。

    旧运行及44篇商业结果保持历史；新运行冻结本地政策与排序提示，可复用已有摘要文本。
    暂停的本地运行必须由人工明确恢复，不因入口调用自行恢复。
    """
    from agent4pharma.infrastructure.connectors.selection_abstracts import read_selection_abstracts
    from agent4pharma.infrastructure.llm.local_decider import LocalDecider
    from agent4pharma.infrastructure.persistence.paper_selection_repository import PaperSelectionRepository
    from agent4pharma.infrastructure.persistence.sqlite.batch_intelligence import BatchIntelligenceRepository
    from agent4pharma.modules.discovery.news_preferences import default_institution_preferences
    from agent4pharma.modules.discovery.news_selection import (
        NEWS_COMPARISON_OUTPUT_CONTRACT,
        NEWS_SELECTION_VERSION,
    )
    from agent4pharma.modules.discovery.paper_selection import create_selection_run
    from agent4pharma.modules.discovery.triage_contracts import TriagePolicy
    from agent4pharma.runtime.locking import worker_lock
    from agent4pharma.shared.clock import utcnow
    from agent4pharma.workflows.newsroom_standards import (
        selection_audience_instructions,
        selection_prompt_instructions,
    )
    from agent4pharma.workflows.select_local_papers import screen_local_papers

    selections = PaperSelectionRepository(config.data_dir)
    ranking, comparison = "", ""
    if not run_id:
        catalog = build_prompt_repository(config.data_dir).catalog(
            ["news_ranking", "news_comparison", "audience"]
        )
        current = {
            key: next(v for v in versions if v.status in {"published", "builtin"})
            for key, versions in catalog.items()
        }
        audience = selection_audience_instructions(current["audience"])
        ranking = selection_prompt_instructions(current["news_ranking"]) + audience
        comparison = (
            selection_prompt_instructions(current["news_comparison"])
            + audience
            + NEWS_COMPARISON_OUTPUT_CONTRACT
        )
    with worker_lock(config.data_dir / "intelligence"):
        existing = selections.latest(batch_id)
        previous = selections.get(run_id) if run_id else existing[-1] if existing else None
        if previous and previous.batch_id != batch_id:
            raise ValueError("运行不属于指定批次")
        if (
            previous is None
            or previous.selection_method == "legacy"
            or previous.missing_abstract_policy != "best_available"
            or previous.news_selection_version != NEWS_SELECTION_VERSION
            or previous.comparison_thinking_mode != "disabled"
            or previous.paused_journals != config.paused_journals
            or (not run_id and previous.ranking_instructions != ranking)
            or (not run_id and previous.comparison_instructions != comparison)
        ):
            if run_id:
                raise ValueError("旧运行只供回放；迁移请按批次建立本地摘要运行")
            batch = BatchIntelligenceRepository(config.data_dir)
            requests = (
                [i.request.model_copy(deep=True) for i in [*previous.items, *previous.paused_items]]
                if previous
                else batch.pending(
                    batch_id,
                    build_selection_policy(config.data_dir),
                    config.llm.model,
                    max(1, batch.remaining(batch_id)),
                )
            )
            from agent4pharma.infrastructure.persistence.paper_download_repository import (
                PaperDownloadRepository,
            )
            from agent4pharma.modules.discovery.processed_papers import paper_keys, processed_paper_keys

            ledger = PaperDownloadRepository(config.data_dir)
            processed = processed_paper_keys(ledger.attempts())
            skipped = [r.evidence[0].id for r in requests if paper_keys(r) & processed]
            requests = [r for r in requests if not paper_keys(r) & processed]
            run = create_selection_run(
                batch_id,
                requests,
                batch.selection_details(batch_id),
                utcnow(),
                min(5, config.paper_shortlist_limit),
                paused_journals=config.paused_journals,
            )
            run.processed_record_ids = skipped
            run.selection_method = "local_decider_v1"
            run.missing_abstract_policy = "best_available"
            run.local_revision = config.triage.model_revision
            run.local_policy = (
                TriagePolicy.model_validate_json(config.triage.policy_json)
                if config.triage.policy_json
                else TriagePolicy(
                    instructions=build_prompt_repository(config.data_dir).current("triage").text()
                )
            )
            run.ranking_instructions = ranking
            run.news_selection_version = NEWS_SELECTION_VERSION
            run.institution_preferences = default_institution_preferences()
            run.comparison_instructions = comparison
            run.comparison_thinking_mode = "disabled"
            if (
                previous
                and previous.selection_method == "local_decider_v1"
                and previous.local_revision == run.local_revision
                and previous.local_policy is not None
            ):
                # 复用摘要和冻结主题政策；新输入增加作者单位，分类与排序统一重新计算。
                run.local_policy = previous.local_policy.model_copy(deep=True)
                run.reused_abstracts_from = previous.id
                old_items = {i.request.evidence[0].id: i for i in [*previous.items, *previous.paused_items]}
                for item in [*run.items, *run.paused_items]:
                    old = old_items[item.request.evidence[0].id]
                    item.abstract_evidence = (
                        old.abstract_evidence.model_copy(deep=True) if old.abstract_evidence else None
                    )
            identity = run.model_dump_json(exclude={"id", "created_at", "revision"})
            run.id = "selection-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
            selections.save(run)
        else:
            run = previous
        if retry_models:
            run.comparison_attempts = 0
            for item in run.items:
                if item.state == "review_failed":
                    item.review_attempts = 0
        from agent4pharma.infrastructure.llm.news_comparison import CommercialNewsComparison

        news_model = (
            CommercialNewsComparison(os.environ["DEEPSEEK_API_KEY"], config.llm.timeout_seconds)
            if os.environ.get("DEEPSEEK_API_KEY")
            else None
        )
        if news_model is not None and run.comparison_result is None:
            run.comparison_transport_version = getattr(news_model, "protocol_version", "")
        try:
            screen_local_papers(
                run,
                selections,
                LocalDecider(config.triage.endpoint, run.local_revision, config.triage.timeout_seconds),
                read_selection_abstracts,
                abstract_budget=abstract_budget,
                review_budget=review_budget,
                news_model=news_model,
            )
        finally:
            run.processing_stage, run.activity_updated_at = "idle", utcnow()
            selections.save(run)
        return run


def manually_select_paper(root: Path, run_id: str, record_id: str) -> None:
    """人工将准确运行论文加入精选；工作锁内幂等追加，不调用模型或改变模型排名。"""
    from agent4pharma.infrastructure.persistence.paper_selection_repository import PaperSelectionRepository
    from agent4pharma.modules.discovery.paper_selection import manually_select_paper as select
    from agent4pharma.runtime.locking import worker_lock
    from agent4pharma.shared.clock import utcnow

    with worker_lock(root / "intelligence"):
        store = PaperSelectionRepository(root)
        run = store.get(run_id)
        select(run, record_id, utcnow())
        store.save(run)


def build_manual_pdf_imports(root: Path) -> ManualPdfImports:
    """装配项目、Chrome 和系统下载位置及绑定论文关联，不触发外部访问。"""
    from agent4pharma.infrastructure.persistence.manual_pdf_inbox import ManualPdfInbox
    from agent4pharma.infrastructure.persistence.paper_download_repository import PaperDownloadRepository
    from agent4pharma.infrastructure.persistence.paper_selection_repository import PaperSelectionRepository
    from agent4pharma.modules.discovery.paper_selection import require_pdf_association
    from agent4pharma.modules.materials.manual_pdf_imports import ManualPdfImports
    from agent4pharma.modules.materials.paper_downloads import PaperDownloadRequest, PaperDownloadResult
    from agent4pharma.workflows.selected_paper_download import download_request

    inbox = ManualPdfInbox(root)

    def binding(run_id: str, record_id: str) -> PaperDownloadRequest:
        run = PaperSelectionRepository(root).get(run_id)
        return download_request(run, require_pdf_association(run, record_id))

    def import_file(run_id: str, record_id: str, file_id: str) -> PaperDownloadResult:
        return PaperDownloadResult.model_validate(
            import_selected_paper(root, run_id, record_id, inbox.resolve(file_id))
        )

    return ManualPdfImports(
        str(inbox.directory),
        binding,
        PaperDownloadRepository(root).verified,
        inbox.candidates,
        import_file,
        directories=[str(path) for path in inbox.directories],
        warnings=lambda: list(dict.fromkeys(inbox.warnings)),
    )


def build_paper_continuation(
    config: ApplicationSettings, executor: Executor | None = None
) -> PaperContinuationAPI:
    """人工推荐后直接制作；确认和主文身份留档，不再调用全文推荐评估。"""
    from threading import RLock

    from agent4pharma.entrypoints.web.paper_continuation_api import PaperContinuationAPI
    from agent4pharma.infrastructure.persistence.paper_download_repository import PaperDownloadRepository
    from agent4pharma.infrastructure.persistence.paper_selection_repository import PaperSelectionRepository
    from agent4pharma.modules.discovery.paper_selection import require_pdf_association
    from agent4pharma.runtime.locking import worker_lock
    from agent4pharma.shared.clock import utcnow
    from agent4pharma.workflows.continue_imported_paper import continue_imported_paper
    from agent4pharma.workflows.imported_paper_recommendation import (
        confirm_imported_recommendation,
        imported_recommendation_candidate,
    )
    from agent4pharma.workflows.selected_paper_download import download_request

    progress_lock = RLock()
    root = config.data_dir
    store = PaperSelectionRepository(root)
    production = build_production_workbench(root, config.llm.model, config.llm.base_url)

    def status(run_id: str, record_id: str) -> str:
        run = store.get(run_id)
        item = require_pdf_association(run, record_id)
        if PaperDownloadRepository(root).verified(download_request(run, item)) is None:
            raise ValueError("没有可供处理的已校验主文")
        if item.human_recommendation is None:
            raise ValueError("尚未人工确认推荐")
        return item.continuation_status

    def progress(run_id: str, record_id: str, stage: str, task_id: str, error: str) -> None:
        with progress_lock, worker_lock(root / "intelligence"):
            run = store.get(run_id)
            item = require_pdf_association(run, record_id)
            item.continuation_status = stage
            if task_id or stage != "failed":
                item.continuation_task_id = task_id
            item.continuation_updated_at, item.continuation_error = utcnow(), error
            store.save(run)

    def confirm(run_id: str, record_id: str) -> None:
        with progress_lock, worker_lock(root / "intelligence"):
            run = store.get(run_id)
            item = require_pdf_association(run, record_id)
            paused = {" ".join(name.split()).casefold() for name in config.paused_journals}
            if item.human_recommendation is None and " ".join(item.journal.split()).casefold() in paused:
                raise ValueError("该期刊已暂停使用，暂不接受新的人工推荐")
            verified = PaperDownloadRepository(root).verified(download_request(run, item))
            if verified is None:
                raise ValueError("没有可供推荐的已校验主文")
            confirm_imported_recommendation(run, record_id, verified)
            store.save(run)

    def prepare_candidate(run_id: str, record_id: str) -> str:
        run = store.get(run_id)
        item = require_pdf_association(run, record_id)
        verified = PaperDownloadRepository(root).verified(download_request(run, item))
        if verified is None:
            raise ValueError("主文文件已变化，需要重新校验")
        return imported_recommendation_candidate(run, record_id, verified).id

    def execute(run_id: str, record_id: str) -> None:
        continue_imported_paper(
            lambda: prepare_candidate(run_id, record_id),
            production,
            lambda task_id: execute_production_task(root, task_id),
            lambda stage, task_id, error: progress(run_id, record_id, stage, task_id, error),
        )

    return PaperContinuationAPI(status, progress, execute, confirm, executor)


def build_maintenance_agent_tools(
    root: Path, job_id: str, tools: InvestigationTools | None = None
) -> MaintenanceAgentTools:
    """装配来源调查与具名建议工具，不提供旧情报复测或准则发布。"""
    from agent4pharma.infrastructure.persistence.sqlite.directory_inspection import DirectoryInspector
    from agent4pharma.modules.maintenance.agent_tools import MaintenanceAgentTools

    return MaintenanceAgentTools(
        tools or build_investigation_tools(root, job_id),
        build_change_repository(root),
        DirectoryInspector(root).inspect,
    )


def build_selection_policy(root: Path) -> IntelligencePolicyVersion:
    """将当前共用主题标准冻结为精选输入身份，不创建旧情报准则或任务。"""
    from agent4pharma.modules.discovery.intelligence_contracts import (
        IntelligencePolicy,
        IntelligencePolicyVersion,
    )

    scope = build_prompt_repository(root).current("screening_scope")
    return IntelligencePolicyVersion(
        id=scope.id,
        version=scope.version,
        status=scope.status,
        content_hash=scope.content_hash,
        definition=IntelligencePolicy(instructions=scope.text(), scope_version="ai-biology-v1"),
    )


def build_regression_repository(root: Path) -> ArticleRegressionRepository:
    """装配旧单篇回归只读仓储，不提供创建或续跑能力。"""
    from agent4pharma.infrastructure.persistence.sqlite.article_regression_repository import (
        RegressionRepository,
    )

    return RegressionRepository(root)


def build_wechat_delivery(app: ProductionWorkbench) -> WechatDeliveryService:
    """启动边界读取公众号凭证；不连接微信，不把密钥放入配置快照。"""
    from agent4pharma.infrastructure.web.wechat_drafts import WechatDraftClient
    from agent4pharma.workflows.wechat_delivery import WechatDeliveryService

    appid = os.environ.get("PHARMA_WECHAT_APP_ID", "").strip()
    secret = os.environ.get("PHARMA_WECHAT_APP_SECRET", "").strip()
    name = os.environ.get("PHARMA_WECHAT_ACCOUNT_NAME", "公众号").strip() or "公众号"
    gateway = WechatDraftClient(appid, secret) if appid and secret else None
    return WechatDeliveryService(app.unit, app.read_unit, gateway, appid, name)


def build_evidence_delivery(epoch_path: Path) -> EvidenceDelivery:
    """装配跨进程可见代次读取和会话内原文覆盖，文件仅由执行器维护。"""
    return EvidenceDelivery(lambda: read_visibility_epoch(epoch_path))
