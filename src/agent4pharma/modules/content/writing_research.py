"""围绕已立项报道的作者研究底稿；与最终稿件分开保存，不代表独立业务阶段。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SerializerFunctionWrapHandler, model_serializer

from agent4pharma.modules.content.draft_contracts import ResearchEvidence
from agent4pharma.modules.content.news_outline import NewsOutline, validate_outline_sources
from agent4pharma.modules.content.research_contracts import ResearchFinding


class WritingResearchGap(BaseModel):
    """尚未解决的报道事实问题及处理方式；科研展望不自动成为制作待办。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    question: str = Field(min_length=5, max_length=200)
    handling: Literal["omit", "qualify", "block"]
    reason: str = Field(min_length=5, max_length=400)


class WritingResearch(BaseModel):
    """作者对选题的回答、论文依据和必要背景；没有背景缺口可以零搜索。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    question: str = Field(min_length=5, max_length=200)
    answer: str = Field(min_length=20, max_length=1600)
    findings: list[ResearchFinding] = Field(min_length=1, max_length=8)
    background: list[ResearchFinding] = Field(default_factory=list, max_length=5)
    gaps: list[WritingResearchGap] = Field(default_factory=list, max_length=5)
    research_scope: str = Field(min_length=10, max_length=2000)
    claim_check: str = Field(default="", max_length=2000)
    outline: NewsOutline | None = None

    @model_serializer(mode="wrap")
    def serialize_research(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        """省略未记录的核对与骨架，历史底稿不补造产物或改变序列化。"""
        result = handler(self)
        if not result["claim_check"]:
            result.pop("claim_check")
        if result["outline"] is None:
            result.pop("outline")
        return result


class WritingResearchSnapshot(BaseModel):
    """宿主绑定准确任务书与保存版本；模型不提供身份或宣称阶段已完成。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int = Field(ge=1)
    task_id: str
    story_id: str
    brief_version: int
    angle: str
    created_at: str
    research: WritingResearch


def validate_writing_research(result: WritingResearch, evidence: list[ResearchEvidence]) -> None:
    """检查主论文与背景分工、实际证据版本和窗口；不冒充事实语义审查。"""
    sources = {source.id: source for source in evidence}
    if result.outline is not None:
        validate_outline_sources(result.outline, evidence)
    for finding in [*result.findings, *result.background]:
        source = sources.get(finding.evidence_id)
        span = finding.source_span
        if source is None or source.content_hash != span.content_hash:
            raise ValueError("写作底稿引用了未绑定或版本不符的证据")
        if not 0 <= span.start < span.end <= len(source.text):
            raise ValueError("写作底稿原文范围无效")
    if any(
        sources[f.evidence_id].origin in {"web_search_snippet", "web_background_page"}
        for f in result.findings
    ):
        raise ValueError("论文事实须由绑定论文支持，网页片段或背景正文放在background")
    if any(f.source_relation == "external_commentary" for f in result.findings):
        raise ValueError("外部评论应放在background，不能作为原论文结果")
    if any(f.kind == "background" for f in result.findings):
        raise ValueError("论文事实与背景须分开保存")
    if any(f.kind != "background" for f in result.background):
        raise ValueError("背景事实须标记kind=background")
