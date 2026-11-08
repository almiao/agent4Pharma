"""内部工作台应用协调：保存人工样本、快照策略、排队调查，界面只调用此层和公开契约。"""

import hashlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from agent4pharma.modules.discovery.intelligence_contracts import IntelligenceStore
from agent4pharma.modules.discovery.screening_policy import PolicyCatalog
from agent4pharma.modules.maintenance.change_contracts import MaintenanceChanges
from agent4pharma.modules.maintenance.execution import InvestigationAgent
from agent4pharma.modules.maintenance.investigation_tools import InvestigationTools
from agent4pharma.modules.maintenance.models import InvestigationJob, InvestigationRequest, InvestigationStart
from agent4pharma.modules.maintenance.repository import MaintenanceRepository
from agent4pharma.modules.sources.management import SourceCatalog
from agent4pharma.runtime.settings import ModelSettings
from agent4pharma.workflows.investigate_article import investigate_article


class MaintenanceWorkbench:
    """单用户本地应用；一次执行一个调查，避免桌面/模型资源互相争抢。"""

    def __init__(
        self,
        sources: SourceCatalog,
        policies: PolicyCatalog,
        cases: MaintenanceRepository,
        tools_factory: Callable[[str], InvestigationTools],
        agent: InvestigationAgent | None,
        model_name: str,
        model_available: Callable[[], bool],
        prompt_hash: str = "",
        intelligence_store: IntelligenceStore | None = None,
        model_settings: ModelSettings | None = None,
        changes: MaintenanceChanges | None = None,
        prompt_source: Callable[[], str] | None = None,
    ):
        self.sources, self.policies, self.cases = sources, policies, cases
        self.tools_factory, self.agent = tools_factory, agent
        self.model_name, self.model_available = model_name, model_available
        self.prompt_hash = prompt_hash
        self.prompt_source = prompt_source
        self.intelligence_store, self.changes = intelligence_store, changes
        self.model_settings = model_settings or ModelSettings()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="source-investigation")
        self._submitted: set[str] = set()
        self._lock = Lock()

    def start_investigation(self, case_id: str, options: InvestigationStart) -> InvestigationJob:
        """先保存不可变请求再排队；重复请求不重复发起网络或模型调用。"""
        if options.mode == "agent" and (self.agent is None or not self.model_available()):
            raise ValueError("未配置 DEEPSEEK_API_KEY；可先使用不调用模型的规则探查")
        case, policy = self.cases.get_case(case_id), self.policies.get_policy(options.policy_id)
        prompt = self.prompt_source() if options.mode == "agent" and self.prompt_source else ""
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest() if prompt else self.prompt_hash
        request = InvestigationRequest(
            case=case,
            options=options,
            policy=policy,
            model_name=self.model_name if options.mode == "agent" else "",
            prompt_hash=prompt_hash if options.mode == "agent" else "",
            prompt=prompt,
            model_base_url=self.model_settings.base_url,
            model_timeout=self.model_settings.timeout_seconds,
            model_max_tokens=self.model_settings.max_tokens,
        )
        job = self.cases.create_run(request)
        with self._lock:
            if job.status == "queued" and job.id not in self._submitted:
                self._submitted.add(job.id)
                self._executor.submit(self._execute, job.id)
        return self.cases.get_job(job.id)

    def _execute(self, job_id: str) -> None:
        try:
            investigate_article(self.tools_factory(job_id), self.agent)
        except Exception as exc:
            # 隔离后台任务错误，禁止将 SDK 原始诊断或凭证写入持久化错误。
            self.cases.update_job(job_id, "failed", error="调查进程错误：" + type(exc).__name__)

    def close(self) -> None:
        """停止接收新任务；已运行任务受页面/模型超时限制并保留完成状态。"""
        self._executor.shutdown(wait=False, cancel_futures=True)
