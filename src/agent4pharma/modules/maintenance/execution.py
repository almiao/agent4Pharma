"""模型调查的运行契约，工作流不依赖 DSH 的具体 SDK 类型。"""

from typing import Protocol


class InvestigationAgent(Protocol):
    """受限模型调查执行器；结果必须经绑定任务的工具保存。"""

    def run(self, job_id: str) -> None:
        """在有限时间内调查并提交结构化结论，失败向调用方抛错。"""
        ...
