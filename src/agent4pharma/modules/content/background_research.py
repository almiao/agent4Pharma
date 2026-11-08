"""主编与作者的背景调研中间文档：问题、理由、证据与针对报道的充分性判断。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent4pharma.modules.content.draft_contracts import ResearchEvidence
from agent4pharma.modules.content.research_contracts import ResearchFinding


class BackgroundQuestion(BaseModel):
    """一个服务新闻判断或读者理解的问题；未解决与技术失败不冒充答案。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    question: str = Field(min_length=5, max_length=240)
    purpose: Literal["decision", "explanation"]
    relevance: str = Field(min_length=10, max_length=600)
    status: Literal["planned", "answered", "partial", "unresolved", "technical_failure", "not_needed"]
    answer: str = Field(default="", max_length=2000)
    findings: list[ResearchFinding] = Field(default_factory=list, max_length=5)
    handling: Literal["use", "qualify", "omit", "block"]
    assessment: str = Field(min_length=10, max_length=800)

    @model_validator(mode="after")
    def require_supported_answers(self) -> "BackgroundQuestion":
        """已回答须有实质答案及引用；省略、未解决或技术失败不能伪装已可采用。"""
        if self.status in {"answered", "partial"} and (len(self.answer.strip()) < 10 or not self.findings):
            raise ValueError("已回答或部分回答的背景问题须有答案和证据")
        if self.handling == "use" and self.status != "answered":
            raise ValueError("未完整回答的背景问题不能标记直接采用")
        if any(finding.kind != "background" for finding in self.findings):
            raise ValueError("背景调研的依据须标记kind=background")
        return self


class BackgroundResearch(BaseModel):
    """针对本篇的研究问题与产出；允许零问题，不按条数或搜索次数认定充分。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    focus: str = Field(min_length=10, max_length=600)
    questions: list[BackgroundQuestion] = Field(default_factory=list, max_length=8)
    sufficiency: Literal["in_progress", "sufficient", "limited", "insufficient"]
    assessment: str = Field(min_length=10, max_length=1200)
    research_scope: str = Field(min_length=10, max_length=1600)

    @model_validator(mode="after")
    def validate_assessment(self) -> "BackgroundResearch":
        """拒绝重复问题及明显自相矛盾的完成判断；不替代语义充分性审阅。"""
        if len({question.id for question in self.questions}) != len(self.questions):
            raise ValueError("背景问题ID不可重复")
        if self.sufficiency != "in_progress" and any(q.status == "planned" for q in self.questions):
            raise ValueError("仍有待调研问题时应标记调研中")
        if self.sufficiency in {"sufficient", "limited"} and any(
            q.handling == "block" for q in self.questions
        ):
            raise ValueError("有核心背景阻断时不能标记已足够")
        return self


class BackgroundResearchSnapshot(BaseModel):
    """宿主绑定任务、阶段与准确任务书；保存点不是阶段完成或人工审核通过。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str
    phase: str
    story_id: str = ""
    brief_version: int = 0
    version: int = Field(ge=1)
    created_at: str
    research: BackgroundResearch


def validate_background_research(result: BackgroundResearch, evidence: list[ResearchEvidence]) -> None:
    """引用只允许绑定证据的准确版本与连续范围，不证明结论或充分性判断正确。"""
    sources = {source.id: source for source in evidence}
    for question in result.questions:
        for finding in question.findings:
            span = finding.source_span
            source = sources.get(finding.evidence_id)
            if source is None or source.content_hash != span.content_hash:
                raise ValueError("背景调研引用了未绑定或版本不符的证据")
            if not 0 <= span.start < span.end <= len(source.text):
                raise ValueError("背景调研原文范围无效")
