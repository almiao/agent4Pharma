"""主题词表与检索式编译；词命中不再决定已采集资料的去留。"""

import re
import unicodedata
from typing import Protocol

from pydantic import BaseModel, Field, model_validator


class ScreeningPolicy(BaseModel):
    """可版本化的复测策略；不把传统 ML 或裸 agent 作为放行依据。"""

    name: str = Field(default="Agent/大模型＋制药", min_length=1, max_length=120)
    scope_version: str = "agent-llm-pharma-v1"
    agent_terms: list[str] = Field(
        default_factory=lambda: [
            "AI agent",
            "AI agents",
            "LLM agent",
            "LLM agents",
            "LLM-based agent",
            "agentic workflow",
            "agentic system",
            "multi-agent system",
            "智能体",
            "多智能体",
        ],
        max_length=100,
    )
    model_terms: list[str] = Field(
        default_factory=lambda: [
            "large language model",
            "large language models",
            "LLM",
            "LLMs",
            "large multimodal model",
            "大语言模型",
            "多模态大模型",
        ],
        max_length=100,
    )
    drug_terms: list[str] = Field(
        default_factory=lambda: [
            "drug discovery",
            "drug design",
            "drug development",
            "drug repurposing",
            "virtual screening",
            "molecular docking",
            "molecular generation",
            "lead optimization",
            "drug target",
            "ADMET",
            "antibody design",
            "protein design",
            "drug delivery",
            "clinical trial",
            "药物发现",
            "药物设计",
            "制药",
            "抗体设计",
            "分子生成",
            "药物研发",
        ],
        max_length=200,
    )
    review_terms: list[str] = Field(
        default_factory=lambda: [
            "foundation model",
            "protein language model",
            "molecular foundation model",
            "蛋白质大模型",
        ],
        max_length=100,
    )
    scope: str = Field(default="Agent/大模型须实际参与制药研发；不收传统 ML-only 研究。", max_length=3000)
    notes: str = Field(default="词命中不代表主题已通过；蛋白/分子非语言基础模型进入待审。", max_length=3000)

    @model_validator(mode="after")
    def validate_terms(self) -> "ScreeningPolicy":
        """规范词组并拒绝空词表、超长词及会误放行的泛词。"""
        for field in ("agent_terms", "model_terms", "drug_terms", "review_terms"):
            terms = list(dict.fromkeys(t.strip() for t in getattr(self, field) if t.strip()))
            if any(len(t) > 150 for t in terms):
                raise ValueError("单个词组最长 150 字符")
            setattr(self, field, terms)
        forbidden = {"agent", "agents", "ai", "ml", "machine learning", "deep learning", "model"}
        if self.scope_version != "ai-biology-v1" and any(
            t.casefold() in forbidden for t in self.agent_terms + self.model_terms
        ):
            raise ValueError("请使用明确的 Agent/大模型短语，不能以裸 agent、AI 或传统 ML 放行")
        if not self.drug_terms or not (self.agent_terms or self.model_terms):
            raise ValueError("至少需要一组 Agent/大模型词和一组制药任务词")
        return self


class PolicyVersion(BaseModel):
    """不可变策略内容及其发布状态；修改内容创建新版本。"""

    id: str
    version: int
    status: str
    definition: ScreeningPolicy
    content_hash: str
    created_at: str


class GateResult(BaseModel):
    """词表命中预览，只用于检索式/人工核对，不能充当模型判断或过滤依据。"""

    route: str
    agent_hits: list[str] = Field(default_factory=list)
    model_hits: list[str] = Field(default_factory=list)
    drug_hits: list[str] = Field(default_factory=list)
    review_hits: list[str] = Field(default_factory=list)
    reason: str


class PolicyCatalog(Protocol):
    """工作台策略版本管理契约；发布供新运行使用，旧批次保留快照。"""

    def list_versions(self) -> list[PolicyVersion]:
        """返回全部草案和历史版本。"""
        ...

    def get_policy(self, policy_id: str | None = None) -> PolicyVersion:
        """取得指定或默认复测版本。"""
        ...

    def save_policy(self, definition: ScreeningPolicy) -> PolicyVersion:
        """以不可变新版本保存编辑。"""
        ...

    def publish_policy(self, policy_id: str) -> PolicyVersion:
        """原子切换工作台当前策略。"""
        ...


def normalize_text(text: str) -> str:
    """统一大小写、Unicode、连字符及空白，保留词边界语义。"""
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[-‐‑‒–—]+", " ", text))


def term_hits(text: str, terms: list[str]) -> list[str]:
    """匹配显式词组；英文使用完整词边界，中文按配置短语匹配。"""
    normalized = normalize_text(text)
    hits = []
    for term in terms:
        pattern = re.escape(normalize_text(term))
        if term.isascii():
            pattern = r"(?<![a-z0-9])" + pattern + r"(?![a-z0-9])"
        if re.search(pattern, normalized):
            hits.append(term)
    return hits


def screen_text(policy: ScreeningPolicy, title: str, text: str) -> GateResult:
    """回放工作台词表匹配；仅作检索预览或历史核对，不决定精筛入口。"""
    content = title + "\n" + text
    values = {
        name: term_hits(content, getattr(policy, name + "_terms"))
        for name in ("agent", "model", "drug", "review")
    }
    if (values["agent"] or values["model"]) and values["drug"]:
        route = "llm_candidate"
        reason = (
            "AI 方法与生物/医学主题同时命中，仍需语义精筛"
            if policy.scope_version == "ai-biology-v1"
            else "Agent/大模型与制药任务同时命中，仍需语义精筛"
        )
    elif values["review"] and values["drug"]:
        route, reason = "related_review", "基础模型分支待审，不能自动当作 LLM 核心内容"
    elif len(text.strip()) < 80:
        route, reason = "insufficient_information", "可用正文/摘要不足，不能仅凭缺词判为无关"
    else:
        route, reason = "no_keyword_match", "未同时命中 Agent/大模型及制药任务，不直接进入候选"
    return GateResult(route=route, reason=reason, **{k + "_hits": v for k, v in values.items()})
