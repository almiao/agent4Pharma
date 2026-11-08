"""文章协作编辑的定位、候选及应用契约；字符位置使用Unicode码点。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent4pharma.modules.content.draft_contracts import ClaimEvidence


class TextReference(BaseModel):
    """绑定准确正文的一处选区，不按全文字符串替换。"""

    model_config = ConfigDict(extra="forbid")
    target: str = Field(default="body", pattern=r"^(body|title|caption:[0-9]+)$")
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=4000)


class ArticleEditRequest(BaseModel):
    """一次有界对话，request_id区分显式再生成与重复提交。"""

    model_config = ConfigDict(extra="forbid")
    package_id: str
    draft_id: str
    request_id: str = Field(min_length=1, max_length=100)
    instruction: str = Field(default="", max_length=3000)
    references: list[TextReference] = Field(default_factory=list, max_length=8)
    previous_task_id: str = ""
    auto_apply: bool = False
    figure_indices: list[int] = Field(default_factory=list, max_length=4)


class TextChange(TextReference):
    """替换一个准确范围，可替换为零字符删除；不支持隐式插入。"""

    replacement: str = Field(max_length=12000)


class ManualFigureText(BaseModel):
    """准确包内原图的图注和段落位置；不接收图源或文件路径。"""

    model_config = ConfigDict(extra="forbid")
    caption: str = Field(max_length=500)
    after_paragraph: int = Field(ge=1)


class ManualArticleEdit(BaseModel):
    """人工保存文字、原图位置及来源显示；准确包/稿件绑定基线，不调用模型。"""

    model_config = ConfigDict(extra="forbid")
    package_id: str = Field(min_length=1)
    draft_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=80, max_length=60000)
    figures: list[ManualFigureText] = Field(default_factory=list, max_length=100)
    claim_texts: list[str] | None = Field(default=None, max_length=100)
    excluded_source_ids: list[str] | None = Field(default=None, max_length=100)


class FigureEdit(BaseModel):
    """修改已有图片的位置或移除；替换只接受绑定论文图块。"""

    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=0)
    action: Literal["move", "remove", "replace"]
    after_paragraph: int | None = Field(default=None, ge=1)
    evidence_id: str = ""
    page: int = Field(default=0, ge=0)
    block: int = Field(default=0, ge=0)
    label: str = Field(default="", max_length=100)
    caption: str = Field(default="", max_length=500)
    visual_observation: str = Field(default="", max_length=700)


class EditOption(BaseModel):
    """一个可单独接受的变更集，语义风险由后续核验处理。"""

    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=100)
    explanation: str = Field(min_length=1, max_length=600)
    changes: list[TextChange] = Field(default_factory=list, max_length=12)
    figures: list[FigureEdit] = Field(default_factory=list, max_length=4)
    claims: list[ClaimEvidence] | None = Field(default=None, min_length=1, max_length=100)


class ArticleEditResult(BaseModel):
    """候选和答复，不直接修改稿件；无需修改或证据不足可只答复。"""

    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)
    options: list[EditOption] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def distinct_options(self) -> "ArticleEditResult":
        """拒绝重复候选冒充多种改写。"""
        if len({o.model_dump_json(exclude={"label", "explanation", "claims"}) for o in self.options}) != len(
            self.options
        ):
            raise ValueError("候选变更必须不同")
        return self


def validate_ranges(body: str, references: list[TextReference]) -> None:
    """拒绝错位、重复和重叠选区，保留重复文本的准确位置。"""
    previous = -1
    for item in sorted(references, key=lambda x: x.start):
        if (
            not 0 <= item.start < item.end <= len(body)
            or item.start < previous
            or body[item.start : item.end] != item.text
        ):
            raise ValueError("选区已变、越界或相互重叠，请重新选择")
        previous = item.end


def apply_option(body: str, option: EditOption) -> str:
    """仅替换用户接受的候选范围，逆序执行保持其他字符不变。"""
    validate_ranges(body, option.changes)
    for change in sorted(option.changes, key=lambda x: x.start, reverse=True):
        body = body[: change.start] + change.replacement + body[change.end :]
    if not body.strip():
        raise ValueError("修改后正文不能为空")
    return body
