"""人工推荐的主文交接：校验准确文件、冻结确认身份，制作侧直接读取来源快照。"""

import hashlib

from agent4pharma.modules.content.draft_contracts import ResearchEvidence
from agent4pharma.modules.discovery.paper_selection import (
    HumanPaperRecommendation,
    PaperSelectionRun,
    require_pdf_association,
)
from agent4pharma.modules.materials.document_reading import DocumentReference
from agent4pharma.modules.materials.paper_downloads import PaperDownloadResult
from agent4pharma.shared.clock import utcnow
from agent4pharma.workflows.production_dependencies import CandidateSnapshot, DownloadSelectionBasis
from agent4pharma.workflows.selected_paper_download import download_request


def confirm_imported_recommendation(
    run: PaperSelectionRun, record_id: str, verified: PaperDownloadResult
) -> HumanPaperRecommendation:
    """显式人工确认后创建幂等推荐；只改该条目，不执行模型或覆盖历史终判。

    调用方持运行写锁并负责保存追加版本；文件必须已通过真实主文校验。
    """
    item = require_pdf_association(run, record_id)
    if run.paused:
        raise ValueError("运行已暂停，不能启动推荐制作")
    if (
        verified.status != "downloaded"
        or not verified.identity_match
        or verified.pages < 1
        or verified.request != download_request(run, item)
        or verified.id != item.download_id
        or not verified.sha256
    ):
        raise ValueError("人工推荐必须绑定本篇已校验的主文")
    if item.human_recommendation is not None:
        if (
            item.human_recommendation.document_id != verified.id
            or item.human_recommendation.document_sha256 != verified.sha256
        ):
            raise ValueError("人工推荐的主文已变化，不能沿用原确认")
        return item.human_recommendation
    if item.continuation_status not in {"idle", "queued"}:
        raise ValueError("历史处理已有执行记录，请在新运行中确认推荐")
    identity = "\n".join((run.id, record_id, verified.id, verified.sha256))
    item.human_recommendation = HumanPaperRecommendation(
        id="human-paper-" + hashlib.sha256(identity.encode()).hexdigest()[:32],
        record_id=record_id,
        document_id=verified.id,
        document_sha256=verified.sha256,
        confirmed_at=utcnow(),
    )
    return item.human_recommendation


def imported_recommendation_candidate(
    run: PaperSelectionRun, record_id: str, verified: PaperDownloadResult
) -> CandidateSnapshot:
    """将已确认推荐映射为制作输入；无模型终判引用，不声明摘要或整篇主文已读。"""
    item = require_pdf_association(run, record_id)
    recommendation = item.human_recommendation
    if recommendation is None:
        raise ValueError("尚未人工确认推荐")
    if (
        verified.status != "downloaded"
        or not verified.identity_match
        or verified.pages < 1
        or verified.request != download_request(run, item)
        or verified.id != recommendation.document_id
        or verified.id != item.download_id
        or verified.sha256 != recommendation.document_sha256
        or recommendation.record_id != record_id
    ):
        raise ValueError("推荐与主文身份不匹配，需要重新校验")
    primary = item.request.evidence[0]
    abstract = item.abstract_evidence or primary
    card = (
        next(
            (card for card in run.comparison_result.cards if card.record_id == record_id),
            None,
        )
        if run.comparison_result
        else None
    )
    basis = (
        DownloadSelectionBasis(
            news_hook=card.news_hook,
            reason=card.reason,
            evidence_quote=card.evidence_quote,
            institution_quote=card.institution_quote,
        )
        if card
        else None
    )
    return CandidateSnapshot(
        id=recommendation.id,
        assessment_id="",
        summary=primary.title,
        reason="",
        selection_basis=basis,
        evidence=[
            ResearchEvidence(
                id=primary.id,
                title=primary.title,
                text=abstract.text or primary.title,
                url=primary.url,
                origin="human_recommended_paper",
                document=DocumentReference(id=verified.id, sha256=verified.sha256, pages=verified.pages),
                read_scope="人工已关联主文；当前文本仅为标题、摘要或已有来源文本，未声明主文已读",
            )
        ],
    )
