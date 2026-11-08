"""内容交付与证据契约，正文和关键表述绑定准确资料快照。"""

import hashlib
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from agent4pharma.modules.materials.document_reading import DocumentReference


class ResearchEvidence(BaseModel):
    """实际取得的材料；fulltext 标识原文来源，已读范围以 read_scope 为准。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=200)
    title: str
    text: str = Field(min_length=1, max_length=60000)
    url: str = ""
    origin: str
    fulltext: bool = False
    document: DocumentReference | None = None
    read_scope: str = ""
    locators: list[str] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def serialize_evidence(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        """旧快照不加入空文档引用字段，保持任务与证据的原始身份。"""
        result = handler(self)
        for key in ("document", "read_scope", "locators"):
            if not result[key]:
                result.pop(key)
        return result

    @property
    def content_hash(self) -> str:
        """文本快照的 SHA256，只证明版本一致。"""
        return hashlib.sha256(self.text.encode()).hexdigest()


class EvidenceSpan(BaseModel):
    """引用绑定准确文本版本与字符范围，程序从该范围提取逐字原文。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class ClaimEvidence(BaseModel):
    """关键表述及其直接支撑或明确推断依据。"""

    model_config = ConfigDict(extra="forbid")
    claim: str = Field(min_length=1, max_length=2000)
    evidence_id: str
    quote: str = Field(default="", max_length=3000, description="连续原文；使用source_span时留空，由程序提取")
    source_span: EvidenceSpan | None = Field(
        default=None, description="复制find_bound_evidence的content_hash/start/end"
    )
    kind: Literal["fact", "attributed_claim", "inference"] = Field(
        default="fact",
        description="稿件声明类别；数字/比较仍按fact或attributed_claim，不接受研究底稿的number/comparison",
    )
    limitation: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def require_quote(self) -> "ClaimEvidence":
        """保留旧引文输入；新输入至少给出原文或可验证位置。"""
        if not self.quote and self.source_span is None:
            raise ValueError("quote 或 source_span 至少提供一项")
        return self

    @model_serializer(mode="wrap")
    def serialize_claim(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        """不为旧稿加入空定位字段，保持历史稿件 hash。"""
        result = handler(self)
        if self.source_span is None:
            result.pop("source_span", None)
        return result


class EvidenceExcerpt(BaseModel):
    """绑定证据的逐字切片及字符位置；供作者/编辑定位引用，不生成新证据。"""

    evidence_id: str
    content_hash: str
    start: int
    end: int
    text: str


class ReaderRevisionResponse(BaseModel):
    """作者逐项回应阅读问题；保留意见也必须说明理由，不自称编辑已关闭。"""

    model_config = ConfigDict(extra="forbid")
    issue_id: str = Field(min_length=1, max_length=100)
    action: Literal["revised", "retained"]
    reason: str = Field(min_length=5, max_length=1000)


class DraftSubmission(BaseModel):
    """无任意文件路径的稿件包；首版只支持纯文字，不承诺图片许可。"""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=80, max_length=60000)
    summary: str = Field(min_length=1, max_length=3000)
    claims: list[ClaimEvidence] = Field(min_length=1, max_length=100)
    coverage: str = Field(min_length=10, max_length=4000)
    limitations: list[str] = Field(default_factory=list, max_length=20)
    resolved_issue_ids: list[str] = Field(default_factory=list, max_length=100)
    reader_responses: list[ReaderRevisionResponse] = Field(default_factory=list, max_length=20)

    @model_serializer(mode="wrap")
    def serialize_submission(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        """无阅读问题回应的旧稿保持原序列化及历史hash。"""
        result = handler(self)
        if not self.reader_responses:
            result.pop("reader_responses", None)
        return result


class DraftRevision(BaseModel):
    """保存后不可覆盖的准确稿件版本。"""

    id: str
    story_id: str
    brief_version: int
    number: int
    author_id: str
    submission: DraftSubmission
    evidence: list[ResearchEvidence]
    content_hash: str
    created_at: str
    reader_policy: Literal["", "reader-quality-v1"] = ""

    @model_serializer(mode="wrap")
    def serialize_revision(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        """旧稿不补阅读策略字段，历史快照与内容hash不变。"""
        result = handler(self)
        if not self.reader_policy:
            result.pop("reader_policy", None)
        return result


def bind_draft_quotes(draft: DraftSubmission, evidence: list[ResearchEvidence]) -> DraftSubmission:
    """验证版本与范围后提取引文；不猜测、拼接或改写不匹配的引文。"""
    visible = {item.id: item for item in evidence}
    claims = []
    for index, claim in enumerate(draft.claims):
        span = claim.source_span
        if span is None:
            claims.append(claim)
            continue
        source = visible.get(claim.evidence_id)
        if source is None or source.content_hash != span.content_hash:
            raise ValueError(f"claims[{index}].source_span 证据版本不匹配")
        if not 0 <= span.start < span.end <= len(source.text) or span.end - span.start > 3000:
            raise ValueError(f"claims[{index}].source_span 字符范围无效或超出3000字符")
        quote = source.text[span.start : span.end]
        if claim.quote and claim.quote != quote:
            raise ValueError(f"claims[{index}].quote 与 source_span 指向的连续原文不一致")
        claims.append(claim.model_copy(update={"quote": quote}))
    return draft.model_copy(update={"claims": claims})


def validate_draft(draft: DraftSubmission, evidence: list[ResearchEvidence], requires_fulltext: bool) -> None:
    """验证引用真实存在与关键表述可定位；语义充分性仍由独立编辑核验。"""
    draft = bind_draft_quotes(draft, evidence)
    visible = {item.id: item for item in evidence}
    if requires_fulltext and not any(item.fulltext and item.text.strip() for item in evidence):
        raise ValueError("任务要求全文，尚无全文证据；请补充或申请变更任务书")
    errors = []
    for index, claim in enumerate(draft.claims):
        source = visible.get(claim.evidence_id)
        if source is None:
            errors.append(f"claims[{index}].evidence_id 引用的证据不属于绑定任务")
        elif source.origin == "primary_document":
            errors.append(f"claims[{index}].evidence_id 文档目录不是正文证据，请先按需读取具体页")
        elif claim.quote not in source.text:
            errors.append(f"claims[{index}].quote 引用必须从证据复制连续原文，不能用省略号拼接或改写")
        if claim.claim not in draft.body:
            errors.append(f"claims[{index}].claim 必须从 body 逐字复制，不能重新概括")
        if claim.kind == "inference" and not claim.limitation.strip():
            errors.append(f"claims[{index}].limitation：推断必须说明条件与限制")
    if errors:
        raise ValueError("；".join(errors[:12]))
