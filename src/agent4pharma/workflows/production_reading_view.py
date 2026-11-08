"""制作角色的模型上下文投影；任务书、稿件可见，论文只呈现目录。"""

import hashlib
from typing import Literal

from pydantic import BaseModel, Field

from agent4pharma.modules.content.background_research import BackgroundResearchSnapshot
from agent4pharma.modules.content.draft_contracts import (
    DraftSubmission,
    EvidenceSpan,
    ReaderRevisionResponse,
    ResearchEvidence,
)
from agent4pharma.modules.content.writing_research import WritingResearchSnapshot
from agent4pharma.modules.editorial.commission_contracts import Story
from agent4pharma.modules.materials.evidence_catalog import EvidenceCatalogEntry, catalog_entry
from agent4pharma.modules.publishing.article_layout import FigureSelection
from agent4pharma.modules.review.reader_review_contracts import ReaderReviewReport
from agent4pharma.modules.review.review_contracts import ReviewReport
from agent4pharma.workflows.production_dependencies import DownloadSelectionBasis, ProductionContext


class CandidateReadingView(BaseModel):
    """候选保留已生成摘要和理由，引用证据正文须按需取读。"""

    id: str
    assessment_id: str
    summary: str
    reason: str
    evidence: list[EvidenceCatalogEntry]
    selection_basis: DownloadSelectionBasis | None = None


class ClaimReadingView(BaseModel):
    """引文仅给位置目录；审核按需取源文，初始上下文不倾倒全部引文。"""

    claim: str
    evidence_id: str
    source_span: EvidenceSpan | None
    quote_characters: int
    kind: str
    limitation: str


class DraftSubmissionReadingView(BaseModel):
    """保留稿件本身，论文引文作为可读取的引用索引。"""

    title: str
    body: str
    summary: str
    coverage: str
    limitations: list[str]
    resolved_issue_ids: list[str]
    claims: list[ClaimReadingView]
    reader_responses: list[ReaderRevisionResponse] = Field(default_factory=list)


class DraftReadingView(BaseModel):
    """待写或待审稿件可完整呈现，但不嵌入其整套论文文本快照。"""

    id: str
    number: int
    author_id: str
    content_hash: str
    source_body_hash: str
    submission: DraftSubmissionReadingView


class ProductionReadingView(BaseModel):
    """模型读取权限的目录视图，不等同于后台保留的完整任务快照。"""

    candidates: list[CandidateReadingView]
    story: Story | None
    evidence: list[EvidenceCatalogEntry]
    draft: DraftReadingView | None
    reader_reviews: list[ReaderReviewReport] = Field(default_factory=list)
    reviews: list[ReviewReport]
    review_mode: Literal["initial", "reconsideration"]
    reads_remaining: int
    max_pages_per_read: int = 3
    citation_lookups_remaining: int | None = None
    phase: str = ""
    writing_research: WritingResearchSnapshot | None = None
    background_research: list[BackgroundResearchSnapshot] = Field(default_factory=list)
    background_research_expected_version: int = 0
    writing_research_expected_version: int = 0
    missing_required_resources: list[str] = Field(default_factory=list)
    publication_figures: list[FigureSelection] = Field(default_factory=list)
    max_characters_per_read: int = 6000
    tool_input_rules: str = (
        "参数必须是纯JSON，禁止.replace()等表达式。ID和64位content_hash从工具结果完整复制。"
        "稿件claims.kind仅fact/attributed_claim/inference；数字和比较按事实或归因主张分类，"
        "不能复制研究底稿number/comparison。expected_version仅使用当前任务返回值，上游报告版本不计入。"
        "先读missing_required_resources；引用定位额度耗尽后复用已有source_span，不重查。"
    )


def production_reading_view(context: ProductionContext, reads_remaining: int) -> ProductionReadingView:
    """不给模型嵌入 candidates.evidence、evidence 或 draft.evidence 的论文全文。"""
    draft = context.draft
    return ProductionReadingView(
        candidates=[
            CandidateReadingView(
                id=c.id,
                assessment_id=c.assessment_id,
                summary=c.summary,
                reason=c.reason,
                selection_basis=c.selection_basis,
                evidence=[catalog_entry(e) for e in c.evidence],
            )
            for c in context.candidates
        ],
        story=context.story,
        evidence=[catalog_entry(e) for e in context.evidence],
        draft=DraftReadingView(
            id=draft.id,
            number=draft.number,
            author_id=draft.author_id,
            content_hash=draft.content_hash,
            source_body_hash=hashlib.sha256(draft.submission.body.encode()).hexdigest(),
            submission=_draft_view(draft.submission, draft.evidence),
        )
        if draft
        else None,
        reviews=context.reviews,
        reader_reviews=context.reader_reviews,
        review_mode=context.review_mode,
        reads_remaining=reads_remaining,
        phase=context.phase,
        writing_research=context.writing_research,
        background_research=context.background_research,
    )


def _draft_view(draft: DraftSubmission, evidence: list[ResearchEvidence]) -> DraftSubmissionReadingView:
    sources = {e.id: e for e in evidence}

    def span_for(evidence_id: str, quote: str) -> EvidenceSpan | None:
        source = sources.get(evidence_id)
        if source is None or not quote:
            return None
        start = source.text.find(quote)
        return (
            EvidenceSpan(content_hash=source.content_hash, start=start, end=start + len(quote))
            if start >= 0
            else None
        )

    return DraftSubmissionReadingView(
        title=draft.title,
        body=draft.body,
        summary=draft.summary,
        coverage=draft.coverage,
        limitations=draft.limitations,
        resolved_issue_ids=draft.resolved_issue_ids,
        reader_responses=draft.reader_responses,
        claims=[
            ClaimReadingView(
                claim=c.claim,
                evidence_id=c.evidence_id,
                source_span=c.source_span or span_for(c.evidence_id, c.quote),
                quote_characters=len(c.quote),
                kind=c.kind,
                limitation=c.limitation,
            )
            for c in draft.claims
        ],
    )
