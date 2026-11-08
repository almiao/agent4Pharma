"""通过只读公开网页/证据 MCP 执行 DSH 调查，不授予 Shell 或任意文件权限。"""

import os
import uuid
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from agent4pharma.infrastructure.llm.dsh_execution_trace import DshExecutionTrace
from agent4pharma.infrastructure.llm.patch import create_patch
from agent4pharma.infrastructure.llm.provider_endpoint import dsh_base_url
from agent4pharma.infrastructure.llm.run_observation import record_model_result
from agent4pharma.infrastructure.persistence.sqlite.article_regression_repository import ObservationRepository
from agent4pharma.infrastructure.persistence.sqlite.investigations import InvestigationRepository
from agent4pharma.runtime.settings import ApplicationSettings


@dataclass
class DshInvestigationRunner:
    """为一次调查启动隔离 DSH，会话有请求时限，证据和结论经 MCP 校验入库。"""

    config_path: Path
    config: ApplicationSettings

    def run(self, job_id: str) -> None:
        """执行有界模型任务；模型最终文本不作为已验证的结构化结论。"""
        from deepseek_harness import DeepSeekHarness

        prompt = InvestigationRepository(self.config.data_dir).get_job(job_id).request.prompt or (
            files("agent4pharma.agents.source_maintenance")
            .joinpath("system_prompt.md")
            .read_text(encoding="utf-8")
        )
        patch = create_patch(
            self.config_path,
            self.config,
            job_id,
            prompt,
            mcp_args=[
                "-m",
                "agent4pharma",
                "--config",
                str(self.config_path.resolve()),
                "investigation-mcp",
                "--root",
                str(self.config.data_dir.resolve()),
                "--job",
                job_id,
            ],
        )
        workspace = self.config.data_dir / "agent" / "investigations"
        workspace.mkdir(parents=True, exist_ok=True)
        trace = DshExecutionTrace(
            ObservationRepository(self.config.data_dir),
            job_id,
            "maintenance",
            "source-review-" + uuid.uuid4().hex,
            self.config.llm.model,
            secrets=(os.environ.get("DEEPSEEK_API_KEY", ""),),
        )
        with DeepSeekHarness(
            dsh_home=str((self.config.data_dir / "agent" / "dsh-home").resolve()),
            cwd=str(workspace.resolve()),
            profile="sdk-minimal",
            env=trace.environment(),
            patches=(str(patch.resolve()),),
            provider="deepseek-official",
            model=self.config.llm.model,
            base_url=dsh_base_url(self.config.llm.base_url),
            request_timeout_seconds=min(self.config.llm.timeout_seconds, 180),
            initialize_timeout_seconds=60,
            max_tokens=self.config.llm.max_tokens,
        ) as harness:
            result = trace.run(
                harness,
                f"调查任务 {job_id}。核对原始来源、本地资料匹配和来源适用性，最后调用 save_findings。",
                bound_input={"system_prompt_prefix": prompt, "context_binding": None},
            )
            record_model_result(self.config.data_dir, job_id, "maintenance", result)
