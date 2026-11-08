"""管理 HTTP 路由：结构化校验输入，不执行 SQL，不直接初始化模型。"""

import hashlib
from collections.abc import Callable
from threading import Lock
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from agent4pharma.composition import (
    background_activity_status,
    build_collection_plan_catalog,
    build_dependency_controller,
    build_dependency_monitor,
    build_discovery_query_cache,
    build_manual_pdf_imports,
    discovery_article_reading,
    manually_select_paper,
    paper_selection_page,
    render_workbench_queries,
)
from agent4pharma.entrypoints.web.prompt_api import PromptAPI
from agent4pharma.modules.discovery.screening_policy import ScreeningPolicy, screen_text
from agent4pharma.modules.maintenance.models import ArticleCaseInput, InvestigationJob, InvestigationStart
from agent4pharma.modules.materials.manual_pdf_imports import ManualPdfConfirmation
from agent4pharma.modules.sources.management import SourceDirectoryFilters
from agent4pharma.runtime.dependency_status import DependencyControl, DependencyControlRequest
from agent4pharma.workflows.maintenance_workbench import MaintenanceWorkbench


class ManualPaperSelection(BaseModel):
    """只允许按准确运行与来源版本加入精选，不接受排名或模型结论。"""

    model_config = ConfigDict(extra="forbid")
    run_id: str
    record_id: str


class PreviewInput(BaseModel):
    """词表编辑预览不落库、不联网、不调用模型。"""

    definition: ScreeningPolicy
    title: str = Field(default="", max_length=1000)
    text: str = Field(default="", max_length=60000)


class CompareInput(BaseModel):
    """比较两版策略对同一缓存样本的词粗筛，不能充当 LLM 或上游召回验证。"""

    case_id: str
    left_policy_id: str
    right_policy_id: str


class ArticleSubmission(BaseModel):
    """日常只需链接或全文，模型模式显式由分析按钮启动。"""

    content: str = Field(min_length=1, max_length=60000)
    mode: Literal["agent", "rules"] = "agent"
    request_id: str = Field(default="", max_length=100, pattern=r"^[a-zA-Z0-9_-]*$")


class ProposalDecision(BaseModel):
    """人工独立确认一条具体变更，模型工具没有此能力。"""

    accept: bool


def job_summary(job: InvestigationJob) -> dict[str, JsonValue]:
    """历史列表只传摘要，完整正文证据按任务读取，避免一次输出全部历史原件。"""
    return {
        "id": job.id,
        "status": job.status,
        "case_id": job.request.case.id,
        "title": job.request.case.title or job.request.case.reference,
        "mode": job.request.options.mode,
        "policy_version": job.request.policy.version,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "error": job.error,
        "page_count": len(job.result.pages) if job.result else 0,
    }


class WorkbenchAPI:
    """显式路由工作台操作；非幂等操作只通过 JSON POST 执行。"""

    def __init__(
        self,
        app: MaintenanceWorkbench,
        on_pdf_imported: Callable[[str, str], str] | None = None,
        decider_endpoint: str = "",
    ):
        self.app = app
        self.on_pdf_imported = on_pdf_imported
        self.dependencies = build_dependency_monitor(decider_endpoint, app.cases.root)
        self.discovery_queries = build_discovery_query_cache(app.cases.root)
        self._dependency_control: DependencyControl | None = None
        self._dependency_lock = Lock()

    @property
    def dependency_control(self) -> DependencyControl:
        """仅在服务状态/控制入口使用时装配本机控制资源，其他业务查询不创建执行器。"""
        with self._dependency_lock:
            if self._dependency_control is None:
                self._dependency_control = build_dependency_controller(self.app.cases.root, self.dependencies)
        return self._dependency_control

    def close(self) -> None:
        """释放已装配的控制资源，不因关闭而创建资源或终止独立依赖。"""
        if self._dependency_control is not None:
            self._dependency_control.close()
        self.discovery_queries.close()

    def get(self, path: str) -> JsonValue:
        """读取目录、策略、样本和任务；不存在的路径抛出 KeyError。"""
        parts = urlsplit(path)
        route = parts.path
        query = parse_qs(parts.query)
        if route == "/api/paper-selections":
            return paper_selection_page(
                self.app.cases.root, query.get("batch", [""])[0], query.get("run", [""])[0]
            )
        if route == "/api/paper-selections/downloads":
            from agent4pharma.composition import paper_selection_downloads

            return paper_selection_downloads(self.app.cases.root, query.get("batch", [""])[0])
        if route == "/api/dependencies":
            return self.dependency_control.snapshot(self.app.model_available()).model_dump(mode="json")
        if route == "/api/dependencies/mineru-tasks":
            return self.dependencies.parser_tasks().model_dump(mode="json")
        if route == "/api/background-activity":
            return background_activity_status(self.app.cases.root, self.app.cases.list_jobs()).model_dump(
                mode="json"
            )
        if route == "/api/manual-pdfs/directory":
            imports = build_manual_pdf_imports(self.app.cases.root)
            return {
                "directory": imports.directory,
                "directories": imports.directories,
                "warnings": imports.warnings(),
            }
        if route == "/api/prompts":
            return PromptAPI(self.app.cases.root, self.app.model_name).get()
        if route == "/api/discovery-run":
            return self.discovery_queries.get(query.get("batch", [""])[0])
        if route == "/api/discovery-run/article":
            return discovery_article_reading(
                self.app.cases.root,
                query.get("batch", [""])[0],
                query.get("record", [""])[0],
                int(query.get("offset", ["0"])[0]),
            ).model_dump(mode="json")
        if route == "/api/status":
            return {
                "model": self.app.model_name,
                "model_available": self.app.model_available(),
                "scope": self.app.policies.get_policy().definition.scope,
                "scope_version": self.app.policies.get_policy().definition.scope_version,
                "notice": "日常已采用全来源本地 Decider 预选、价值排序与商业整体比较；关键词只编译检索式。"
                "分析调用来源维护与情报模型；变更经你确认后生效。",
            }
        if route == "/api/collection-plans":
            return {
                "items": [
                    p.model_dump(mode="json")
                    for p in build_collection_plan_catalog(self.app.cases.root).list_plans()
                ]
            }
        if route == "/api/intelligence-tasks":
            parent = query.get("batch", [""])[0]
            if "task_ids" in query:
                return {
                    "items": [
                        t.model_dump(mode="json")
                        for t in self.app.intelligence_store.summaries_for_ids(
                            query["task_ids"][0].split(",")
                        )
                    ]
                }
            return {
                "items": [
                    t.model_dump(mode="json")
                    for t in (
                        self.app.intelligence_store.run_summaries(parent)
                        if parent
                        else self.app.intelligence_store.recent_summaries()
                    )
                ]
            }
        if route.startswith("/api/intelligence-tasks/"):
            return self.app.intelligence_store.get(route.rsplit("/", 1)[1]).model_dump(mode="json")
        if route == "/api/sources":
            filters = SourceDirectoryFilters.model_validate(
                {key: query[key][0] for key in ("availability", "kind", "status", "offset") if key in query}
            )
            search = query.get("q", [""])[0][:200]
            return {
                "items": [
                    s.model_dump(mode="json")
                    for s in self.app.sources.list_sources(search, 200, filters=filters)
                ],
                "kinds": self.app.sources.source_kinds(),
                "has_more": bool(
                    self.app.sources.list_sources(
                        search, 1, filters=filters.model_copy(update={"offset": filters.offset + 200})
                    )
                ),
                "offset": filters.offset,
            }
        if route == "/api/policies":
            return {"items": [p.model_dump(mode="json") for p in self.app.policies.list_versions()]}
        if route == "/api/intelligence-policies":
            return {"items": [p.model_dump(mode="json") for p in self.app.intelligence_store.versions()]}
        if route == "/api/candidates":
            return {"items": self.app.intelligence_store.candidates(query.get("batch", [""])[0])}
        if route == "/api/maintenance-requests":
            return {
                "items": [
                    r.model_dump(mode="json") for r in self.app.intelligence_store.maintenance_requests()
                ]
            }
        if route == "/api/proposals":
            return {"items": [p.model_dump(mode="json") for p in self.app.changes.list_proposals()]}
        if route == "/api/cases":
            return {"items": [c.model_dump(mode="json") for c in self.app.cases.list_cases()]}
        if route == "/api/jobs":
            return {"items": [job_summary(j) for j in self.app.cases.list_jobs()]}
        if route.startswith("/api/jobs/"):
            identity = route.rsplit("/", 1)[1]
            payload = self.app.cases.get_job(identity).model_dump(mode="json")
            payload["proposals"] = [
                p.model_dump(mode="json") for p in self.app.changes.list_proposals(identity)
            ]
            return payload
        if route.startswith("/api/cases/"):
            return self.app.cases.get_case(route.rsplit("/", 1)[1]).model_dump(mode="json")
        raise KeyError("接口不存在")

    def post(self, route: str, payload: JsonValue) -> JsonValue:
        """校验修改输入，所有目录和模型操作遵循各自的业务契约。"""
        if route == "/api/dependencies/control":
            request = DependencyControlRequest.model_validate(payload)
            return self.dependency_control.control(request).model_dump(mode="json")
        if route == "/api/paper-selections/manual":
            request = ManualPaperSelection.model_validate(payload)
            manually_select_paper(self.app.cases.root, request.run_id, request.record_id)
            return {"status": "selected"}
        if route == "/api/manual-pdfs/confirm":
            confirmation = ManualPdfConfirmation.model_validate(payload)
            if confirmation.recommend_and_start and self.on_pdf_imported is None:
                raise ValueError("未装配人工推荐后的采编入口")
            result = (
                build_manual_pdf_imports(self.app.cases.root).confirm(confirmation).model_dump(mode="json")
            )
            if result["status"] == "imported" and confirmation.recommend_and_start and self.on_pdf_imported:
                result["followup_id"] = self.on_pdf_imported(confirmation.run_id, confirmation.record_id)
            return result
        if route.startswith("/api/prompts/"):
            return PromptAPI(self.app.cases.root, self.app.model_name).post(route, payload)
        if route.startswith("/api/maintenance-requests/") and route.endswith("/investigate"):
            return self._investigate_feedback(route.split("/")[-2])
        if route == "/api/analyze":
            return self._analyze(ArticleSubmission.model_validate(payload))
        if route.startswith("/api/proposals/") and route.endswith("/resolve"):
            decision = ProposalDecision.model_validate(payload)
            return self.app.changes.resolve(route.split("/")[-2], decision.accept).model_dump(mode="json")
        if route == "/api/policies":
            return self.app.policies.save_policy(ScreeningPolicy.model_validate(payload)).model_dump(
                mode="json"
            )
        if route.startswith("/api/policies/") and route.endswith("/publish"):
            return self.app.policies.publish_policy(route.split("/")[-2]).model_dump(mode="json")
        if route == "/api/policies/preview":
            values = PreviewInput.model_validate(payload)
            return {
                "screening": screen_text(values.definition, values.title, values.text).model_dump(
                    mode="json"
                ),
                "queries": render_workbench_queries(values.definition),
            }
        if route == "/api/cases":
            return self.app.cases.save_case(ArticleCaseInput.model_validate(payload)).model_dump(mode="json")
        if route.startswith("/api/cases/") and route.endswith("/run"):
            return self.app.start_investigation(
                route.split("/")[-2], InvestigationStart.model_validate(payload)
            ).model_dump(mode="json")
        if route.startswith("/api/cases/"):
            return self.app.cases.save_case(
                ArticleCaseInput.model_validate(payload), route.rsplit("/", 1)[1]
            ).model_dump(mode="json")
        if route == "/api/compare":
            return self._compare(CompareInput.model_validate(payload))
        raise KeyError("接口不存在")

    def _investigate_feedback(self, task_id: str) -> JsonValue:
        request = next(
            (r for r in self.app.intelligence_store.maintenance_requests() if r.task_id == task_id), None
        )
        if request is None:
            raise KeyError("维护请求不存在")
        identity = "followup-" + task_id
        try:
            return self.app.cases.get_job("investigate-" + identity).model_dump(mode="json")
        except KeyError:
            pass
        task = self.app.intelligence_store.get(task_id)
        evidence = task.request.evidence[0]
        case = self.app.cases.save_case(
            ArticleCaseInput(
                reference=evidence.url or "情报反馈:" + task_id,
                text=evidence.text,
                title=evidence.title,
                reason="情报请求复核：" + request.reason,
            )
        )
        return self.app.start_investigation(
            case.id, InvestigationStart(mode="agent", request_id=identity)
        ).model_dump(mode="json")

    def _analyze(self, values: ArticleSubmission) -> JsonValue:
        content = values.content.strip()
        if not content:
            raise ValueError("请粘贴文章链接或全文")
        is_url = content.startswith(("http://", "https://")) and not any(c.isspace() for c in content)
        reference = content if is_url else "人工粘贴:" + hashlib.sha256(content.encode()).hexdigest()[:16]
        text = "" if is_url else content
        if values.request_id:
            try:
                previous = self.app.cases.get_job("investigate-" + values.request_id)
            except KeyError:
                previous = None
            if previous:
                if (
                    previous.request.case.reference != reference
                    or previous.request.case.text != text
                    or previous.request.options.mode != values.mode
                ):
                    raise ValueError("request_id 已用于其他文章")
                return previous.model_dump(mode="json")
        if values.mode == "agent" and not self.app.model_available():
            raise ValueError("未配置模型凭证，尚不能自动理解与调查；可在高级选项使用无模型探查")
        case = self.app.cases.save_case(ArticleCaseInput(reference=reference, text=text))
        return self.app.start_investigation(
            case.id, InvestigationStart(mode=values.mode, request_id=values.request_id)
        ).model_dump(mode="json")

    def _compare(self, change: CompareInput) -> JsonValue:
        case = self.app.cases.get_case(change.case_id)
        text, title = case.text, case.title or case.reference
        if not text:
            run = next(
                (
                    j
                    for j in self.app.cases.list_jobs()
                    if j.request.case.id == case.id
                    and j.request.case.reference == case.reference
                    and j.result
                ),
                None,
            )
            page = next((p for p in run.result.pages if p.status == "fetched"), None) if run else None
            if page:
                text, title = page.text, case.title or page.title
        left, right = (
            self.app.policies.get_policy(identity)
            for identity in (change.left_policy_id, change.right_policy_id)
        )
        return {
            "case_id": case.id,
            "notice": "仅比较相同缓存输入的词粗筛，没有模型或网络请求",
            "left_version": left.version,
            "right_version": right.version,
            "left": screen_text(left.definition, title, text).model_dump(mode="json"),
            "right": screen_text(right.definition, title, text).model_dump(mode="json"),
        }
