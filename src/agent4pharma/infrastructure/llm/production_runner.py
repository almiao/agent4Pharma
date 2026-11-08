"""有硬进程时限的 DSH 制作适配器，凭证仅继承环境，不写任务或 patch。"""

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agent4pharma.infrastructure.llm.dsh_native_support import (
    configured_sharp_native,
    native_dependency_identity,
)
from agent4pharma.infrastructure.llm.patch import create_patch
from agent4pharma.infrastructure.llm.provider_endpoint import dsh_base_url
from agent4pharma.infrastructure.llm.run_observation import record_model_result
from agent4pharma.infrastructure.llm.runtime_build_identity import runtime_build_identity
from agent4pharma.modules.content.draft_contracts import ResearchEvidence
from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.runtime.production_failure import (
    CUMULATIVE_LIMIT_MESSAGE,
    TOOL_MOUNT_MESSAGE,
    ProductionModelFailure,
    production_failure_detail,
)
from agent4pharma.runtime.settings import ApplicationSettings
from agent4pharma.runtime.workflow_observation import read_observations


@dataclass
class DshProductionRunner:
    """父进程限制角色运行总时长；子进程与其 MCP 共享持久化任务租约。"""

    root: Path
    is_current: Callable[[ProductionTask], bool] | None = None

    def run(self, task: ProductionTask) -> None:
        """显式调用时才检查模型凭证并创建 SDK 子进程，输出不回显供应商诊断。"""
        if not os.environ.get("DEEPSEEK_API_KEY"):
            raise ValueError("未配置模型凭证")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agent4pharma",
                "production-worker",
                "--root",
                str(self.root.resolve()),
                "--task",
                task.id,
                "--lease",
                task.lease_token,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **options,
        )
        deadline = time.monotonic() + task.limits.timeout_seconds
        try:
            while True:
                if self.is_current is not None and not self.is_current(task):
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("DSH 制作达到硬超时")
                try:
                    code = process.wait(timeout=min(0.5, remaining))
                except subprocess.TimeoutExpired:
                    continue
                if code != 0:
                    tool_marker = self.root / "agent" / f"{task.id}-{task.attempt}.tools-unavailable"
                    if task.context_tools and tool_marker.exists():
                        raise ProductionModelFailure(TOOL_MOUNT_MESSAGE)
                    budget_marker = self.root / "agent" / f"{task.id}-{task.attempt}.context-epoch.budget"
                    if task.revision_window_policy and budget_marker.exists():
                        raise ProductionModelFailure(CUMULATIVE_LIMIT_MESSAGE)
                    from agent4pharma.infrastructure.persistence.sqlite.article_regression_repository import (
                        ObservationRepository,
                    )

                    events = read_observations(
                        ObservationRepository(self.root), [task.id], ("model_call", "agent_run")
                    )
                    diagnostic = production_failure_detail(events, task.attempt)
                    if diagnostic:
                        raise ProductionModelFailure(diagnostic)
                    raise RuntimeError("DSH 制作子进程失败")
                return
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                else:
                    process.kill()
                process.wait()


def run_production_worker(
    root: Path, task: ProductionTask, save_search: Callable[[list[ResearchEvidence]], None] | None = None
) -> None:
    """在隔离工作目录执行冻结的模型/提示词/输出上限，不读取现行配置替换快照。"""
    from deepseek_harness import DeepSeekHarness
    from deepseek_harness.models import Notification

    started = time.monotonic()
    window_policy = (
        task.revision_window_policy
        if (
            task.phase == "draft-writing"
            and "skills/draft-writing/core/revision.md" in task.required_resources
        )
        else None
    )
    epoch_path = root / "agent" / f"{task.id}-{task.attempt}.context-epoch"
    if window_policy:
        epoch_path.parent.mkdir(parents=True, exist_ok=True)
        epoch_path.write_text("0", encoding="utf-8")
    config = ApplicationSettings(data_dir=root)
    uses_images = task.role == "publisher" or (task.phase == "article-revision" and task.web_search_enabled)
    sharp_native = configured_sharp_native(root) if uses_images else None
    patch = create_patch(
        root / "production-config.toml",
        config,
        task.id,
        task.prompt,
        mcp_args=[
            "-m",
            "agent4pharma",
            "production-mcp",
            "--root",
            str(root.resolve()),
            "--task",
            task.id,
            "--lease",
            task.lease_token,
        ],
        mcp_cwd=root,
        tool_timeout_ms=95000,
        web_search=task.web_search_enabled,
        newsroom_context=bool(task.newsroom_policy),
        research_context=(
            task.phase == "draft-writing"
            and task.newsroom_skills is not None
            and task.newsroom_skills.context_policy in {"research-checkpoint-v1", "research-checkpoint-v2"}
        ),
        sharp_native=sharp_native,
        revision_window_policy=window_policy,
        required_tool_names=task.context_tools,
    )
    from agent4pharma.infrastructure.llm.dsh_execution_trace import DshExecutionTrace
    from agent4pharma.infrastructure.llm.production_telemetry import ProductionTelemetry
    from agent4pharma.infrastructure.persistence.sqlite.article_regression_repository import (
        ObservationRepository,
    )
    from agent4pharma.runtime.workflow_observation import WorkflowObservation
    from agent4pharma.shared.clock import utcnow

    if sharp_native:
        ObservationRepository(root).append(
            WorkflowObservation(
                id=f"{task.id}-{task.attempt}-native-image",
                subject_ref=task.id,
                stage=task.role,
                operation="native_image_runtime",
                status="succeeded",
                created_at=utcnow(),
                output_json=json.dumps(
                    {
                        "sharp_version": "0.35.5",
                        "native_sha256": native_dependency_identity(sharp_native),
                        "adapter": "windows-sharp-snapshot-path-v1",
                    }
                ),
            )
        )
    if task.context_tools:
        ObservationRepository(root).append(
            WorkflowObservation(
                id=f"{task.id}-{task.attempt}-window-runtime",
                subject_ref=task.id,
                stage=task.role,
                operation="context_window_runtime",
                status="succeeded",
                created_at=utcnow(),
                output_json=json.dumps(
                    {
                        "attempt": task.attempt,
                        "policy": window_policy.model_dump() if window_policy else None,
                        "runtime": runtime_build_identity().model_dump(),
                    }
                ),
            )
        )
    telemetry = ProductionTelemetry(
        ObservationRepository(root),
        task.id,
        task.role,
        task.attempt,
        save_search if task.web_search_enabled else None,
    )

    trace = DshExecutionTrace(
        ObservationRepository(root),
        task.id,
        task.role,
        task.id + "-" + str(task.attempt),
        task.model,
        task.attempt,
        (os.environ.get("DEEPSEEK_API_KEY", ""),),
    )

    def observe(notification: Notification) -> None:
        if (
            notification.method == "session.event"
            and notification.payload.get("sessionId") == trace.identity.session_id
        ):
            event = notification.payload.get("event")
            if isinstance(event, dict):
                telemetry.event(event)

    workspace = root / "agent" / task.role
    workspace.mkdir(parents=True, exist_ok=True)

    def may_repair() -> bool:
        from agent4pharma.infrastructure.persistence.sqlite.production_unit import production_unit

        with production_unit(root, read_only=True) as work:
            current = work.tasks.get(task.id)
        return (
            current.status == "running"
            and current.lease_token == task.lease_token
            and datetime.fromisoformat(current.lease_until) > datetime.now(timezone.utc)
        )

    with DeepSeekHarness(
        dsh_home=str((root / "agent" / "dsh-home").resolve()),
        cwd=str(workspace.resolve()),
        profile="sdk-minimal",
        patches=(str(patch.resolve()),),
        env={
            **trace.environment(),
            "PHARMA_TOOL_GUARD_MARKER": str(
                (root / "agent" / f"{task.id}-{task.attempt}.tools-unavailable").resolve()
            ),
            **({"PHARMA_CONTEXT_EPOCH": str(epoch_path.resolve())} if window_policy else {}),
            **({"PHARMA_DSH_SHARP_NATIVE": str(sharp_native)} if sharp_native else {}),
        },
        provider="deepseek-official",
        model=task.model,
        reasoning_effort=task.limits.reasoning_effort,
        base_url=dsh_base_url(task.endpoint),
        request_timeout_seconds=task.limits.timeout_seconds,
        initialize_timeout_seconds=min(60, task.limits.timeout_seconds),
        max_tokens=task.limits.max_tokens,
    ) as harness:
        result = trace.run(
            harness,
            "读取绑定制作任务并提交本角色的结构化产物；无法完成则 report_production_blocked。",
            bound_input={"system_prompt_prefix": task.prompt, "context_binding": json.loads(task.input_json)},
            on_notification=observe,
            repair_malformed=may_repair,
        )
        record_model_result(root, task.id, task.role, result)
        ObservationRepository(root).append(
            WorkflowObservation(
                id=f"{task.id}-{task.attempt}-model-elapsed",
                subject_ref=task.id,
                stage=task.role,
                operation="model_execution",
                status="succeeded",
                created_at=utcnow(),
                output_json=json.dumps(
                    {
                        "attempt": task.attempt,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "finish_reason": result.finish_reason,
                    }
                ),
            )
        )
