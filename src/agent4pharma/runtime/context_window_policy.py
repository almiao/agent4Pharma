"""冻结返修会话的活动窗口与累计执行预算，历史任务缺字段时不启用。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RevisionWindowPolicy(BaseModel):
    """预算针对一次执行尝试，重试用量由生命周期报告另外汇总。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal["revision-window-v1"] = "revision-window-v1"
    threshold_tokens: int = Field(default=40000, ge=12000)
    retain_tokens: int = Field(default=12000, ge=2000)
    max_compactions: int = Field(default=1, ge=0, le=4)
    max_requests: int = Field(default=32, ge=1)
    max_total_tokens: int = Field(default=800000, ge=10000)
    max_output_tokens: int = Field(default=32000, ge=1000)

    @model_validator(mode="after")
    def validate_retention(self) -> "RevisionWindowPolicy":
        """保留窗必须小于触发窗，避免产生不可收敛的压缩配置。"""
        if self.retain_tokens >= self.threshold_tokens:
            raise ValueError("保留窗口必须小于压缩阈值")
        return self
