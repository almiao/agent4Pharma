"""制作管理 HTTP 适配；用户制作动作直接启动自动链路，读取不触发模型。"""

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from agent4pharma.modules.content.article_editing import ArticleEditRequest, ManualArticleEdit
from agent4pharma.modules.materials.evidence_catalog import catalog_entry
from agent4pharma.modules.publishing.article_layout import LayoutRequest
from agent4pharma.modules.publishing.human_approval import ApprovePackage
from agent4pharma.modules.publishing.wechat_delivery import WechatConfirmAbsent, WechatDraftRequest
from agent4pharma.runtime.production_cost import estimate_production_cost
from agent4pharma.runtime.production_execution import ProductionLimits, ProductionTask
from agent4pharma.runtime.production_failure import production_failure_detail
from agent4pharma.runtime.production_lifecycle_cost import lifecycle_task_ids, summarize_lifecycle_cost
from agent4pharma.runtime.production_usage import ProductionUsageView, summarize_usage
from agent4pharma.runtime.workflow_observation import ObservationSink, WorkflowObservation, read_observations
from agent4pharma.workflows.article_editing import (
    AcceptArticleEdit,
    EditStateRequest,
    accept_article_edit,
    article_edit_state,
    queue_article_edit,
)
from agent4pharma.workflows.article_product_editing import (
    ProductEditAction,
    restore_product,
)
from agent4pharma.workflows.background_research import (
    background_research_excerpts,
    background_research_reports,
    background_research_versions,
)
from agent4pharma.workflows.human_package_approval import approve_package
from agent4pharma.workflows.human_review_views import human_review_index
from agent4pharma.workflows.manual_article_editing import save_manual_article
from agent4pharma.workflows.production_context_reports import inspect_task_context
from agent4pharma.workflows.production_evidence import task_evidence
from agent4pharma.workflows.production_queue import PRODUCTION_TRANSITIONS
from agent4pharma.workflows.production_remake import (
    RemakeProduction,
    latest_candidate_tasks,
    paper_remakes,
    remake_paper,
)
from agent4pharma.workflows.production_task_artifacts import production_task_artifact
from agent4pharma.workflows.production_tasks import ControlProductionTask
from agent4pharma.workflows.production_views import production_task_label
from agent4pharma.workflows.production_workbench import ProductionWorkbench
from agent4pharma.workflows.wechat_delivery import WechatDeliveryService
from agent4pharma.workflows.writing_research import (
    current_writing_research,
    writing_research_excerpts,
    writing_research_versions,
)


class WechatStateRequest(BaseModel):
    """读取准确包交付信息；此 POST 无外部发送副作用。"""

    model_config = ConfigDict(extra="forbid")
    package_id: str = Field(min_length=1)


class StartProduction(BaseModel):
    """选定候选和任务预算；确认动作直接进入自动制作。"""

    model_config = ConfigDict(extra="forbid")
    candidate_ids: list[str] = Field(min_length=1, max_length=20)
    limits: ProductionLimits = Field(default_factory=ProductionLimits.for_new_task)


class StartLayout(BaseModel):
    """为准确渠道包启动自动DSH选图任务。"""

    model_config = ConfigDict(extra="forbid")
    package_id: str
    limits: ProductionLimits = Field(default_factory=ProductionLimits.for_new_task)


class RunProduction(BaseModel):
    """用户直接启动准确任务及其后继；不提供逐段授权或任务数放行字段。"""

    task_id: str
    model_config = ConfigDict(extra="forbid")


class ResumeProduction(BaseModel):
    """独立编辑复议请求；工作台提交后自动调用模型。"""

    story_id: str
    role: Literal["reviewer"] = "reviewer"
    limits: ProductionLimits = Field(default_factory=ProductionLimits.for_new_task)


class ProductionAPI:
    """生产查询与后台执行适配器，资源由 HTTP 服务显式关闭。"""

    def __init__(
        self,
        app: ProductionWorkbench,
        execute: Callable[[str], ProductionTask],
        model_available: Callable[[], bool],
        observations: ObservationSink | None = None,
        wechat: WechatDeliveryService | None = None,
    ) -> None:
        self.app, self.execute, self.model_available = app, execute, model_available
        self.wechat = wechat
        self.wechat_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wechat-draft")
        self.wechat_pending: set[str] = set()
        self.observations = observations
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="production")
        self.pending: set[str] = set()
        self.execution_errors: dict[str, str] = {}
        self.lock = Lock()

    def get(self, route: str) -> JsonValue:
        """返回真实快照；租约和模型系统提示词不暴露给浏览器。"""
        parts = urlsplit(route)
        route = parts.path
        if route.startswith("/api/production/tasks/") and route.endswith("/context-summary"):
            task = self.app.execution.get(route.split("/")[-2])
            attempt = int(parse_qs(parts.query).get("attempt", [str(task.attempt)])[0])
            return inspect_task_context(task, self.observations, attempt).model_dump(mode="json")
        if route.startswith("/api/production/tasks/") and route.endswith("/context-manifests"):
            task = self.app.execution.get(route.split("/")[-2])
            query = parse_qs(parts.query)
            after = int(query.get("after", ["0"])[0])
            attempt = int(query.get("attempt", [str(task.attempt)])[0])
            if after < 0 or not 0 <= attempt <= task.attempt:
                raise ValueError("上下文分页或尝试号无效")
            if self.observations is None:
                return {"items": [], "next_cursor": after, "has_more": False}
            page = self.observations.page(
                [task.id], after, operations=("context_manifest", "context_delivery")
            )
            page.items = [
                event for event in page.items if event.execution and event.execution.attempt == attempt
            ]
            return page.model_dump(mode="json")
        if route == "/api/production/paper-status":
            snapshot = self.app.snapshot()
            task_ids = {identity for flow in snapshot.paper_flows for identity in flow.task_ids}
            return {
                "started_candidate_ids": sorted(latest_candidate_tasks(snapshot.tasks)),
                "paper_flows": [flow.model_dump(mode="json") for flow in snapshot.paper_flows],
                "tasks": [
                    task.model_dump(
                        mode="json",
                        include={
                            "id",
                            "story_id",
                            "parent_task_id",
                            "phase",
                            "role",
                            "status",
                            "created_at",
                            "auto_run",
                            "updated_at",
                            "error",
                        },
                    )
                    for task in snapshot.tasks
                    if task.id in task_ids
                ],
            }
        if route == "/api/production/remakes":
            candidate_id = parse_qs(parts.query).get("candidate", [""])[0]
            if not candidate_id:
                raise ValueError("缺少准确人工推荐身份")
            return {"items": [item.model_dump(mode="json") for item in paper_remakes(self.app, candidate_id)]}
        if route == "/api/production/human-review":
            with self.app.read_unit() as work:
                return human_review_index(work, parse_qs(parts.query).get("story", [""])[0]).model_dump(
                    mode="json"
                )
        if route.startswith("/api/production/tasks/") and route.endswith("/observations"):
            identity = route.split("/")[-2]
            self.app.execution.get(identity)
            after = int(parse_qs(parts.query).get("after", ["0"])[0])
            if after < 0:
                raise ValueError("观测游标不能为负数")
            if not self.observations:
                return {"items": [], "next_cursor": after, "has_more": False}
            return self.observations.page([identity], after).model_dump(mode="json")
        if route.startswith("/api/production/tasks/") and route.endswith("/lifecycle-cost"):
            identity = route.split("/")[-2]
            with self.app.read_unit() as work:
                tasks = work.tasks.list()
            ids = lifecycle_task_ids(tasks, identity)
            events = {task_id: [] for task_id in ids}
            if self.observations:
                for event in read_observations(self.observations, list(ids), ("token_usage",)):
                    events[event.subject_ref].append(event)
            return summarize_lifecycle_cost(tasks, events, identity).model_dump(mode="json")
        if route == "/api/production":
            snapshot = self.app.snapshot()
            payload = snapshot.model_dump(mode="json", exclude={"tasks", "contexts"})
            execution = parse_qs(parts.query).get("view", [""])[0] != "summary"
            usage_events: dict[str, list[WorkflowObservation]] = {task.id: [] for task in snapshot.tasks}
            if self.observations:
                for event in read_observations(
                    self.observations, list(usage_events), ("token_usage", "model_execution")
                ):
                    usage_events[event.subject_ref].append(event)
            payload["tasks"] = [
                self._task(task, execution=execution, usage_events=usage_events[task.id])
                for task in snapshot.tasks
            ]
            payload["model_available"] = self.model_available()
            payload["external_delivery"] = False
            payload["transitions"] = [
                edge.model_dump(mode="json") for edge in PRODUCTION_TRANSITIONS.values()
            ]
            payload["execution_errors"] = dict(self.execution_errors)
            labels = {
                "editor": "主编选题",
                "writer": "作者写作",
                "reviewer": "稿件评审",
                "publisher": "图文编排",
            }
            payload["stages"] = [
                {"id": role, "label": labels[role], "model": model}
                for role, model in self.app.stage_models().items()
            ]
            return payload
        if route.startswith("/api/production/tasks/"):
            task = self.app.execution.get(route.rsplit("/", 1)[1])
            with self.app.read_unit() as work:
                artifact = production_task_artifact(work, task)
                research = current_writing_research(task)
                research_excerpts = writing_research_excerpts(
                    task, work.drafts.evidence(task.story_id) if task.story_id else []
                )
                sources = work.drafts.evidence(task.story_id) if task.story_id else task_evidence(task)
                background = background_research_reports(task)
                background_versions = background_research_versions(task)
                background_excerpts = background_research_excerpts(task, sources)
                cited_ids = {
                    finding.evidence_id
                    for report in [*background, *background_versions]
                    for question in report.research.questions
                    for finding in question.findings
                }
            return {
                "task": self._task(task),
                "context_json": task.input_json,
                "prompt": task.prompt,
                "artifact": artifact.model_dump(mode="json") if artifact else None,
                "writing_research": research.model_dump(mode="json") if research else None,
                "writing_research_versions": [
                    r.model_dump(mode="json") for r in writing_research_versions(task)
                ],
                "writing_research_excerpts": [item.model_dump(mode="json") for item in research_excerpts],
                "background_research": [item.model_dump(mode="json") for item in background],
                "background_research_versions": [
                    item.model_dump(mode="json") for item in background_versions
                ],
                "background_research_excerpts": [
                    item.model_dump(mode="json") for item in background_excerpts
                ],
                "background_research_sources": [
                    catalog_entry(source).model_dump(mode="json")
                    for source in sources
                    if source.id in cited_ids
                ],
            }
        if route == "/api/production/artifact-summaries":
            return self.app.artifact_summaries(self.observations).model_dump(mode="json")
        if route == "/api/production/artifacts":
            with self.app.read_unit() as work:
                return {
                    "drafts": [d.model_dump(mode="json") for d in work.drafts.list()],
                    "reviews": [r.model_dump(mode="json") for r in work.reviews.list()],
                    "reader_reviews": [r.model_dump(mode="json") for r in work.reviews.reader_list()],
                    "packages": [p.model_dump(mode="json") for p in work.releases.packages()],
                    "receipts": [r.model_dump(mode="json") for r in work.releases.receipts()],
                }
        raise KeyError("制作接口不存在")

    def post(self, route: str, payload: JsonValue) -> JsonValue:
        """写入通过各业务用例；未配置凭证时不创建付费执行。"""
        if route == "/api/production/wechat-confirm-absent":
            if self.wechat is None:
                raise ValueError("公众号交付服务未装配")
            return self.wechat.confirm_absent(WechatConfirmAbsent.model_validate(payload)).model_dump(
                mode="json"
            )
        if route == "/api/production/wechat-state":
            if self.wechat is None:
                raise ValueError("公众号交付服务未装配")
            request = WechatStateRequest.model_validate(payload)
            return self.wechat.state(request.package_id).model_dump(mode="json")
        if route == "/api/production/wechat-draft":
            if self.wechat is None:
                raise ValueError("公众号交付服务未装配")
            record = self.wechat.queue(WechatDraftRequest.model_validate(payload))
            with self.lock:
                if record.status == "queued" and record.id not in self.wechat_pending:
                    self.wechat_pending.add(record.id)
                    future = self.wechat_executor.submit(self.wechat.execute, record.id)
                    future.add_done_callback(lambda _: self.wechat_pending.discard(record.id))
            return record.model_dump(mode="json")
        if route == "/api/production/remake":
            self._require_model()
            return self._schedule(remake_paper(self.app, RemakeProduction.model_validate(payload)))
        if route == "/api/production/task-control":
            request = ControlProductionTask.model_validate(payload)
            if request.action in {"resume", "retry"}:
                self._require_model()
            task = self.app.execution.control(request)
            return self._schedule(task) if request.action in {"resume", "retry"} else self._task(task)
        if route == "/api/production/approve-package":
            request = ApprovePackage.model_validate(payload)
            with self.app.unit() as work:
                return approve_package(work, request).model_dump(mode="json")
        if route == "/api/production/edit-state":
            request = EditStateRequest.model_validate(payload)
            state = article_edit_state(self.app, request.package_id, request.product)
            if request.product and not request.include_document:
                return state.model_dump(mode="json", exclude={"package", "draft", "versions"})
            return state.model_dump(
                mode="json", exclude={"draft": {"evidence"}, "versions": {"__all__": {"evidence"}}}
            )
        if route == "/api/production/edit-manual":
            return save_manual_article(self.app, ManualArticleEdit.model_validate(payload)).model_dump(
                mode="json"
            )
        if route == "/api/production/edit-restore":
            return restore_product(self.app, ProductEditAction.model_validate(payload)).model_dump(
                mode="json"
            )
        if route == "/api/production/edit-review":
            raise ValueError("人工修改后可直接确认定稿，无需再次调用模型核验；请刷新页面")
        if route == "/api/production/edit-generate":
            request = ArticleEditRequest.model_validate(payload)
            if not self.model_available():
                raise ValueError("模型凭证不可用，未生成修改")
            task = queue_article_edit(self.app, request)
            return self._task(task) if task.status == "failed" else self._schedule(task)
        if route == "/api/production/edit-accept":
            request = AcceptArticleEdit.model_validate(payload)
            self._require_model()
            return self._schedule(accept_article_edit(self.app, request))
        if route == "/api/production/queue-layout":
            request = StartLayout.model_validate(payload)
            self._require_model()
            return self._schedule(self.app.queue_layout(request.package_id, request.limits))
        if route == "/api/production/layout":
            return self.app.create_layout(LayoutRequest.model_validate(payload)).model_dump(mode="json")
        if route == "/api/production/start":
            request = StartProduction.model_validate(payload)
            self._require_model()
            return self._schedule(self.app.start(request.candidate_ids, request.limits))
        if route == "/api/production/prepare-documents":
            if not isinstance(payload, dict) or not isinstance(payload.get("task_id"), str):
                raise ValueError("缺少绑定任务 ID")
            return [item.model_dump(mode="json") for item in self.app.prepare_documents(payload["task_id"])]
        if route == "/api/production/run":
            request = RunProduction.model_validate(payload)
            task = self.app.execution.get(request.task_id)
            if not self.model_available():
                raise ValueError("模型凭证不可用，未启动制作")
            if task.status not in {"queued", "failed"}:
                raise ValueError("任务当前不可执行；运行中任务可在租约过期后经 CLI 恢复")
            if task.attempt >= task.limits.max_attempts:
                raise ValueError("本任务尝试额度已用完，请显式重试创建后继任务")
            if any(t.parent_task_id == task.id for t in self.app.snapshot().tasks):
                raise ValueError("任务已有后继，请执行后继任务")
            self._schedule(task)
            return {"accepted": True, "task_id": task.id}
        if route == "/api/production/resume":
            request = ResumeProduction.model_validate(payload)
            self._require_model()
            return self._schedule(self.app.resume(request.story_id, request.role, request.limits))
        if route == "/api/production/export":
            if not isinstance(payload, dict) or not isinstance(payload.get("package_id"), str):
                raise ValueError("缺少准确渠道包 ID")
            body, extension = self.app.export_package(payload["package_id"], payload.get("variant", "web"))
            return {"body": body, "extension": extension}
        raise KeyError("制作接口不存在")

    def close(self) -> None:
        """不接受新执行；正在运行的任务受硬时限和数据库租约约束。"""
        self.wechat_executor.shutdown(wait=True, cancel_futures=True)
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _task(
        self,
        task: ProductionTask,
        *,
        execution: bool = True,
        usage_events: list[WorkflowObservation] | None = None,
    ) -> dict[str, JsonValue]:
        payload = task.model_dump(
            mode="json",
            exclude={
                "lease_token",
                "prompt",
                "input_json",
                "newsroom_skills",
                "supplemental_evidence_json",
                "document_reads",
                "writing_research_json",
                "background_research_json",
                "revision_workspace_json",
            },
        )

        payload["activity_label"] = production_task_label(task)
        events = (
            usage_events
            if usage_events is not None
            else (
                list(read_observations(self.observations, [task.id], ("token_usage", "model_execution")))
                if self.observations
                else []
            )
        )
        payload["cost"] = estimate_production_cost(events, task.model, task.endpoint).model_dump(mode="json")
        if not execution:
            return payload
        if task.status == "failed" and self.observations:
            diagnostic = production_failure_detail(
                read_observations(self.observations, [task.id], ("model_call", "agent_run")), task.attempt
            )
            if diagnostic:
                payload["error"] = diagnostic
        payload["usage"] = (summarize_usage(events) if events else ProductionUsageView()).model_dump(
            mode="json"
        )
        payload["execution_timings"] = [
            json.loads(event.output_json)
            for event in events
            if event.operation == "model_execution" and event.output_json
        ]
        return payload

    def _require_model(self) -> None:
        if not self.model_available():
            raise ValueError("模型凭证不可用，未启动制作")

    def _schedule(self, task: ProductionTask) -> dict[str, JsonValue]:
        self._require_model()
        if task.status in {"queued", "failed"}:
            task = self.app.execution.request_automatic(task.id)
            with self.lock:
                if task.id not in self.pending:
                    self.pending.add(task.id)
                    self.executor.submit(self._run, task.id)
        return self._task(task)

    def resume_automatic(self) -> None:
        """重启恢复已登记的执行意图；中断中的尝试明确失败，旧未授权排队不补跑。"""
        for task in self.app.snapshot().tasks:
            if not task.auto_run:
                continue
            if task.status == "running":
                self.app.execution.fail_interrupted(task.id, "服务重启中断了执行，请重试此阶段")
            elif task.status == "queued":
                if self.model_available():
                    self._schedule(task)
                else:
                    self.app.execution.fail_interrupted(task.id, "模型凭证不可用，自动制作未能启动")

    def _run(self, task_id: str) -> None:
        requested_id, visited = task_id, set()
        try:
            while True:
                if task_id in visited:
                    raise ValueError("制作后继出现循环，执行已结束")
                visited.add(task_id)
                task = self.app.execution.get(task_id)
                if task.status not in {"queued", "failed"}:
                    return
                self.app.execution.request_automatic(task_id)
                result = self.execute(task_id)
                if result.status != "succeeded":
                    if result.status == "queued":
                        self.app.execution.fail_interrupted(task_id, "执行器未完成任务，制作已结束")
                    return
                next_task = self.app.next_task(result)
                if next_task is None:
                    return
                task_id = next_task.id
        except Exception as exc:
            message = getattr(exc, "public_message", "制作执行失败：" + type(exc).__name__)
            self.execution_errors[requested_id] = message
            self.app.execution.fail_interrupted(task_id, message)
        finally:
            with self.lock:
                self.pending.discard(requested_id)
