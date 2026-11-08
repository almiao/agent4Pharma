"""来源维护 Agent 专用工具：目录调查及保存待审建议。"""

from collections.abc import Callable

from pydantic import JsonValue

from agent4pharma.modules.discovery.screening_policy import ScreeningPolicy, screen_text
from agent4pharma.modules.maintenance.change_contracts import (
    ChangeProposal,
    MaintenanceChange,
    MaintenanceChanges,
)
from agent4pharma.modules.maintenance.investigation_tools import InvestigationTools
from agent4pharma.modules.sources.management import DirectoryInspection


class MaintenanceAgentTools:
    """目录核对与建议，人工确认接口不暴露给模型。"""

    def __init__(
        self,
        tools: InvestigationTools,
        changes: MaintenanceChanges,
        inspect: Callable[[str], DirectoryInspection],
    ):
        self.tools, self.changes, self.inspect = tools, changes, inspect

    def inspect_directory(self, reference: str) -> DirectoryInspection:
        """按实际域名/期刊名称检查登记、正式关注、demo 和入口启用状态。"""
        if not reference.strip() or len(reference) > 2000:
            raise ValueError("目录查询需为 1..2000 字符")
        return self.inspect(reference)

    def propose_change(self, change: MaintenanceChange) -> ChangeProposal:
        """提交具名待确认草案；不能自动发布、启用或确认。"""
        return self.changes.propose(self.tools.job_id, change)

    def preview_coarse_variant(self, definition: ScreeningPolicy) -> dict[str, JsonValue]:
        """以同一可见文章及已有人工正反例做确定性对照，不执行上游查询或 LLM。"""
        job = self.tools.get_investigation()
        report = self.tools.build_report()
        primary = next(
            (p for p in report.pages if p.status == "manual_input"),
            next((p for p in report.pages if p.status == "fetched"), None),
        )
        cases = self.tools.repository.list_cases()[:20]
        samples = [
            {
                "id": c.id,
                "expected": c.expected,
                "before": screen_text(job.request.policy.definition, c.title, c.text).route,
                "after": screen_text(definition, c.title, c.text).route,
            }
            for c in cases
            if c.text
        ]
        return {
            "current": screen_text(definition, primary.title, primary.text).model_dump(mode="json")
            if primary
            else None,
            "samples": samples,
            "notice": "仅缓存词匹配，未知期望不是标签；上游查询和精筛回归需分别验证",
        }
