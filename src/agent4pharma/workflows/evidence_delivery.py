"""同一工具会话的原文覆盖去重；只认准确来源版本和逐字窗口，不猜测跨页语义。"""

from collections.abc import Callable

from pydantic import BaseModel, Field

from agent4pharma.modules.content.draft_contracts import EvidenceExcerpt, ResearchEvidence
from agent4pharma.modules.materials.document_reading import DocumentContinuation
from agent4pharma.runtime.context_records import ContextPayload


class ReusedEvidenceRange(BaseModel):
    """请求中的已交付范围及可复用引用；不把非连续片段伪装成连续原文。"""

    start: int
    end: int
    evidence_id: str
    content_hash: str
    source_start: int
    source_end: int


class ContentReadResult(ContextPayload):
    """text仅在完整交付时存在；发生去重时用带绝对位置的segments与reused表示。"""

    evidence_id: str
    content_hash: str
    start: int
    end: int
    text: str = ""
    segments: list[EvidenceExcerpt] = Field(default_factory=list)
    reused: list[ReusedEvidenceRange] = Field(default_factory=list)
    already_read: bool = False
    truncated: bool = False
    next_request: DocumentContinuation | None = None
    locators: list[str] = Field(default_factory=list)
    next_action: str = "引用位置基于原始证据；上下文已压缩或需完整连续原文时用reread=true。"


class EvidenceDelivery:
    """每个MCP会话独立记录工具交付窗口；不跨会话推定模型记忆，允许显式重读。"""

    def __init__(self, visibility_epoch: Callable[[], str] | None = None) -> None:
        self.windows: list[tuple[str, EvidenceExcerpt]] = []
        self.visibility_epoch = visibility_epoch
        self.epoch = ""

    def deliver(
        self, source: ResearchEvidence, excerpt: EvidenceExcerpt, *, reread: bool = False
    ) -> ContentReadResult:
        """仅同版本或同文档内唯一连续匹配可复用；证据指纹不同且无文档身份则不去重。"""
        if self.visibility_epoch is not None:
            epoch = self.visibility_epoch()
            if epoch != self.epoch:
                self.windows.clear()
                self.epoch = epoch
        doc_hash = source.document.sha256 if source.document else ""
        covered: list[ReusedEvidenceRange] = []
        if not reread:
            for seen_doc, seen in self.windows:
                if seen.content_hash == source.content_hash and (
                    seen.evidence_id == source.id or (doc_hash and doc_hash == seen_doc)
                ):
                    mapped = seen.start
                elif doc_hash and doc_hash == seen_doc and len(seen.text) >= 80:
                    mapped = source.text.find(seen.text)
                    if mapped < 0:
                        # 先读大页窗、后读另一个身份的小片段也可复用；只映射本次实际请求。
                        offset = seen.text.find(excerpt.text) if len(excerpt.text) >= 80 else -1
                        if offset < 0 or seen.text.find(excerpt.text, offset + 1) >= 0:
                            continue
                        mapped = excerpt.start - offset
                    elif source.text.find(seen.text, mapped + 1) >= 0:
                        continue
                else:
                    continue
                left, right = max(excerpt.start, mapped), min(excerpt.end, mapped + len(seen.text))
                if left < right:
                    covered.append(
                        ReusedEvidenceRange(
                            start=left,
                            end=right,
                            evidence_id=seen.evidence_id,
                            content_hash=seen.content_hash,
                            source_start=seen.start + left - mapped,
                            source_end=seen.start + right - mapped,
                        )
                    )
        cursor = excerpt.start
        segments = []
        reused = []
        for part in sorted(covered, key=lambda p: (p.start, -p.end)):
            if part.end <= cursor:
                continue
            if part.start > cursor:
                segments.append(self._slice(excerpt, cursor, part.start))
            left = max(cursor, part.start)
            reused.append(
                part.model_copy(update={"start": left, "source_start": part.source_start + left - part.start})
            )
            cursor = part.end
        if cursor < excerpt.end:
            segments.append(self._slice(excerpt, cursor, excerpt.end))
        # 存储实际交付的新窗口，而不是将整个请求误记为本轮重新交付。
        self.windows.extend((doc_hash, segment) for segment in segments)
        return ContentReadResult(
            evidence_id=excerpt.evidence_id,
            content_hash=excerpt.content_hash,
            start=excerpt.start,
            end=excerpt.end,
            text=excerpt.text if not reused else "",
            segments=segments if reused else [],
            reused=reused,
            already_read=bool(reused) and not segments,
        )

    @staticmethod
    def _slice(excerpt: EvidenceExcerpt, start: int, end: int) -> EvidenceExcerpt:
        return excerpt.model_copy(
            update={
                "start": start,
                "end": end,
                "text": excerpt.text[start - excerpt.start : end - excerpt.start],
            }
        )
