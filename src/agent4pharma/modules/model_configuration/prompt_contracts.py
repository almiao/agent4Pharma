"""节点提示词版本与编辑输入；发布只能改变未来任务采用的版本。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PromptKey = Literal[
    "audience",
    "scientific_evidence",
    "chinese_news_style",
    "screening_scope",
    "triage",
    "news_ranking",
    "news_comparison",
    "editor",
    "writer",
    "reviewer",
    "publisher",
    "maintenance",
]


class PromptDraft(BaseModel):
    """从指定版本复制的新草稿，指令和示例均为普通文本。"""

    model_config = ConfigDict(extra="forbid")
    instructions: str = Field(min_length=40, max_length=24000)
    examples: str = Field(default="", max_length=12000)
    note: str = Field(default="", max_length=1000)

    @field_validator("instructions")
    @classmethod
    def validate_instructions(cls, value: str) -> str:
        """空白文本不能构成可发布指令。"""
        if len(value.strip()) < 40:
            raise ValueError("提示词至少需要 40 个非首尾空白字符")
        return value


class PromptVersion(PromptDraft):
    """不可变正文、版本身份与独立的发布状态。"""

    id: str
    key: PromptKey
    version: int
    status: Literal["builtin", "draft", "published", "archived"]
    created_at: str = ""
    content_hash: str = ""

    def text(self) -> str:
        """生成任务冻结的指令正文；空示例不添加标题。"""
        return self.instructions + ("\n\n参考示例：\n" + self.examples if self.examples else "")


class PromptPublication(BaseModel):
    """发布指定草稿前校验页面看到的当前版本，防止覆盖并发发布。"""

    model_config = ConfigDict(extra="forbid")
    version_id: str
    expected_current: str
    expected_related: dict[Literal["triage"], str] = Field(default_factory=dict)
