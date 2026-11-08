"""新闻骨架与逐段材料依赖；规划不替代正文引用或事实核验。"""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent4pharma.modules.content.draft_contracts import EvidenceSpan, ResearchEvidence


class OutlineSource(BaseModel):
    """一个已读材料窗口及其在段落中的用途，位置须复制绑定资料。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    evidence_id: str = Field(min_length=1, max_length=200)
    source_span: EvidenceSpan
    location: str = Field(
        min_length=1,
        max_length=2000,
        description="复制证据read_scope、一个locator或url；均无则填已绑定文本片段，不猜论文页码",
    )
    use: str = Field(min_length=5, max_length=600, description="这份材料支持本段哪项判断或解释")


class OutlineParagraph(BaseModel):
    """按列表顺序对应计划自然段；纯过渡可依赖前段，不虚构新证据。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    section: str = Field(default="", max_length=160, description="所属部分或拟用小标题，不单算正文段落")
    question: str = Field(min_length=5, max_length=300, description="本段回答的读者问题")
    message: str = Field(min_length=5, max_length=600, description="读者读完新增的理解，不写完整正文")
    position_reason: str = Field(min_length=5, max_length=400, description="为何放在这里，怎样承接或推进")
    sources: list[OutlineSource] = Field(default_factory=list, max_length=8)
    depends_on: list[int] = Field(default_factory=list, max_length=40, description="依赖的前文段号，从1开始")
    visual_need: str = Field(default="", max_length=400, description="可选：原图需要帮助解释什么")


class NewsOutline(BaseModel):
    """整篇新闻承诺、推进结构与逐段依赖；允许写作中有据微调。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    mainline: str = Field(min_length=10, max_length=600)
    progression: str = Field(min_length=10, max_length=800, description="整体框架和理解推进顺序")
    paragraphs: list[OutlineParagraph] = Field(min_length=1, max_length=40)
    ending: str = Field(min_length=5, max_length=400, description="最终回答到哪里，不强制另写总结段")
    omissions: str = Field(default="", max_length=800, description="可选：重要材料不展开的理由")

    @model_validator(mode="after")
    def validate_dependencies(self) -> "NewsOutline":
        """拒绝缺乏依据的计划段与前向/循环依赖，不评判叙事质量。"""
        for number, paragraph in enumerate(self.paragraphs, 1):
            if not paragraph.sources and not paragraph.depends_on:
                raise ValueError(f"骨架第{number}段须引用材料或依赖前文段落")
            if len(set(paragraph.depends_on)) != len(paragraph.depends_on) or any(
                dependency < 1 or dependency >= number for dependency in paragraph.depends_on
            ):
                raise ValueError(f"骨架第{number}段只能依赖不重复的前文段号")
        return self


def validate_outline_sources(outline: NewsOutline, evidence: list[ResearchEvidence]) -> None:
    """只检查实际材料身份、文本窗口及已记录位置，不把页码标签当作语义核验。"""
    sources = {source.id: source for source in evidence}
    for paragraph in outline.paragraphs:
        for dependency in paragraph.sources:
            source = sources.get(dependency.evidence_id)
            span = dependency.source_span
            if source is None or source.content_hash != span.content_hash:
                raise ValueError("新闻骨架引用了未绑定或版本不符的证据")
            if not 0 <= span.start < span.end <= len(source.text):
                raise ValueError("新闻骨架原文范围无效")
            locations = {value for value in [source.read_scope, source.url, *source.locators] if value}
            if dependency.location not in (locations or {"已绑定文本片段"}):
                raise ValueError("新闻骨架位置须复制证据已记录的页窗、定位符或URL，不得猜页码")
