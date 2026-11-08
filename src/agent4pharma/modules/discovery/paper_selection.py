"""摘要筛选与个位数全文清单的契约；容量未入选和下载受阻不是业务排除。"""

import hashlib
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from agent4pharma.modules.discovery.intelligence_contracts import IntelligenceEvidence, IntelligenceRequest
from agent4pharma.modules.discovery.news_preferences import InstitutionPreference
from agent4pharma.modules.discovery.news_selection import NewsComparisonRequest, NewsComparisonResult
from agent4pharma.modules.discovery.triage_contracts import ModelDecision, TriagePolicy
from agent4pharma.modules.materials.paper_downloads import PaperDownloadResult

PRIORITY_JOURNALS = (
    "Nature",
    "Science",
    "Cell",
    "Nature Biotechnology",
    "Nature Methods",
    "Nature Medicine",
    "Nature Machine Intelligence",
    "Nature Computational Science",
    "Cancer Cell",
    "Nature Genetics",
    "Nature Immunology",
    "Nature Structural & Molecular Biology",
    "Nature Communications",
    "Science Advances",
    "Science Translational Medicine",
    "Nature Cancer",
    "Genome Biology",
    "Genome Medicine",
    "Molecular Systems Biology",
    "Cell Systems",
    "Cell Genomics",
    "NEJM AI",
    "npj Digital Medicine",
    "Cell Reports Medicine",
    "Journal of the American Chemical Society",
)


class PaperSelectionDetails(BaseModel):
    """用于初排的实际载体与来源信息，未知值保持为空。"""

    source_id: str = ""
    journal: str = ""
    affiliations: list[str] = Field(default_factory=list)


class HumanPaperRecommendation(BaseModel):
    """人工确认的准确论文及主文身份，不伪装模型评估或已读全文。"""

    id: str
    record_id: str
    document_id: str
    document_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed_at: str
    actor: Literal["local_user"] = "local_user"
    version: Literal["human-paper-v1"] = "human-paper-v1"


class PaperSelectionItem(BaseModel):
    """一份冻结资料及各阶段状态，下载账本与模型结果只保存明确引用。"""

    request: IntelligenceRequest
    source_id: str = ""
    journal: str = ""
    affiliations: list[str] = Field(default_factory=list)
    state: Literal[
        "awaiting_abstract",
        "abstract_missing",
        "local_failed",
        "review_pending",
        "review_failed",
        "review_excluded",
        "abstract_failed",
        "abstract_excluded",
        "eligible",
        "deferred",
        "shortlisted",
        "manual_required",
        "unavailable",
        "download_failed",
        "fulltext_failed",
        "complete",
    ] = "awaiting_abstract"
    abstract_task_id: str = ""
    abstract_evidence: IntelligenceEvidence | None = None
    local_decision: ModelDecision | None = None
    local_margin: float | None = Field(default=None, allow_inf_nan=False)
    local_rank: int | None = None
    review_attempts: int = 0
    relevance_score: int = Field(default=0, ge=0, le=3)
    reason: str = ""
    selection_reason: str = ""
    acquisition_reason: str = ""
    fulltext_reason: str = ""
    continuation_status: Literal[
        "idle",
        "queued",
        "analyzing",
        "editor",
        "writer",
        "reviewer",
        "publisher",
        "succeeded",
        "not_recommended",
        "failed",
        "waiting",
    ] = "idle"
    continuation_task_id: str = ""
    continuation_updated_at: str = ""
    continuation_error: str = ""
    rank: int | None = None
    download_id: str = ""
    manual_selected_at: str = ""
    human_recommendation: HumanPaperRecommendation | None = None
    fulltext_task_id: str = ""
    abstract_attempts: int = 0
    fulltext_attempts: int = 0


class PaperSelectionRun(BaseModel):
    """独立版本化摘要筛选运行；重复续跑不能自动扩大全文额度。"""

    id: str
    batch_id: str
    revision: int = 0
    created_at: str
    shortlist_limit: int = Field(default=5, ge=1, le=9)
    authority_journals: list[str] = Field(default_factory=lambda: list(PRIORITY_JOURNALS))
    institution_preferences: list[InstitutionPreference] = Field(default_factory=list)
    phase: Literal["abstracts", "commercial_review", "fulltexts"] = "abstracts"
    selection_method: Literal["legacy", "local_decider_v1"] = "legacy"
    missing_abstract_policy: Literal["defer", "best_available"] = "defer"
    reused_decisions_from: str = ""
    reused_abstracts_from: str = ""
    reused_comparison_from: str = ""
    local_revision: str = ""
    processing_stage: Literal["idle", "local_abstracts", "local_ranking", "commercial_review"] = "idle"
    activity_updated_at: str = ""
    local_policy: TriagePolicy | None = None
    ranking_instructions: str = ""
    news_selection_version: str = ""
    comparison_instructions: str = ""
    comparison_thinking_mode: Literal["provider_default", "disabled"] = "provider_default"
    comparison_transport_version: str = ""
    comparison_request: NewsComparisonRequest | None = None
    comparison_result: NewsComparisonResult | None = None
    comparison_attempts: int = 0
    comparison_error: str = ""
    review_pool_limit: int = Field(default=10, ge=1, le=10)
    paused: bool = False
    items: list[PaperSelectionItem]
    paused_journals: list[str] = Field(default_factory=list)
    paused_items: list[PaperSelectionItem] = Field(default_factory=list)
    processed_record_ids: list[str] = Field(default_factory=list)
    fulltext_instructions: str = ""


class PaperSelectionStore(Protocol):
    """摘要运行的追加版本与明确来源快照端口。"""

    def save(self, run: PaperSelectionRun) -> None:
        """以当前 revision 比较追加新版本，过期写入必须失败。"""
        ...


def create_selection_run(
    batch_id: str,
    requests: list[IntelligenceRequest],
    details: dict[str, PaperSelectionDetails],
    created_at: str,
    shortlist_limit: int,
    paused_journals: list[str] | None = None,
) -> PaperSelectionRun:
    """冻结全池输入；同资料多来源优先原生摘要更完整者，不按缺摘要排除。"""
    by_material: dict[str, IntelligenceRequest] = {}
    for request in requests:
        old = by_material.get(request.material_ref)
        if old is None or len(request.evidence[0].source_abstract) > len(old.evidence[0].source_abstract):
            by_material[request.material_ref] = request
    items = []
    for request in by_material.values():
        detail = details[request.evidence[0].id]
        items.append(
            PaperSelectionItem(
                request=request.model_copy(deep=True),
                source_id=detail.source_id,
                journal=detail.journal,
                affiliations=detail.affiliations,
            )
        )
    paused_journals = list(paused_journals or [])
    paused = {" ".join(name.split()).casefold() for name in paused_journals}
    paused_items = [item for item in items if " ".join(item.journal.split()).casefold() in paused]
    run = PaperSelectionRun(
        id="pending",
        batch_id=batch_id,
        created_at=created_at,
        shortlist_limit=shortlist_limit,
        items=[item for item in items if " ".join(item.journal.split()).casefold() not in paused],
        paused_journals=paused_journals,
        paused_items=paused_items,
        authority_journals=[
            name for name in PRIORITY_JOURNALS if " ".join(name.split()).casefold() not in paused
        ],
    )
    identity = run.model_dump_json(exclude={"id", "created_at", "revision"})
    run.id = "selection-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
    return run


def require_shortlisted_paper(run: PaperSelectionRun, record_id: str) -> PaperSelectionItem:
    """下载只授权摘要筛选后冻结清单中的准确来源版本；完成状态仍可读取已有 PDF。"""
    item = next((i for i in run.items if i.request.evidence[0].id == record_id), None)
    if (
        run.phase != "fulltexts"
        or item is None
        or item.rank is None
        or not 1 <= item.rank <= run.shortlist_limit
    ):
        raise ValueError("只能下载摘要筛选后进入个位数清单的论文")
    return item


def require_pdf_association(run: PaperSelectionRun, record_id: str) -> PaperSelectionItem:
    """人工关联可覆盖同轮未推荐论文；不修改模型名次或自动下载额度。"""
    item = next((i for i in run.items if i.request.evidence[0].id == record_id), None)
    if run.phase != "fulltexts" or item is None:
        raise ValueError("请等待本轮筛选完成后关联准确论文的 PDF")
    return item


def manually_select_paper(run: PaperSelectionRun, record_id: str, selected_at: str) -> None:
    """人工加入精选，幂等记录时间；调用方持工作锁保存，不启动模型。"""
    item = require_pdf_association(run, record_id)
    if run.paused or run.processing_stage != "idle":
        raise ValueError("请在本轮筛选结束且未暂停时加入精选")
    if not item.manual_selected_at:
        item.manual_selected_at = selected_at


def record_paper_download(run: PaperSelectionRun, record_id: str, result: PaperDownloadResult) -> None:
    """只更新本清单条目的获取结果，不改排序、摘要或已完成的正式终判。"""
    item = require_shortlisted_paper(run, record_id)
    if (
        result.request.decision_id != run.id + ":" + record_id
        or result.request.material_ref != item.request.material_ref
    ):
        raise ValueError("下载结果不属于本条冻结论文")
    item.download_id = result.id
    if item.state != "complete":
        item.state = "shortlisted" if result.status == "downloaded" else "manual_required"
        item.reason = "PDF 已校验，待人工确认推荐" if result.status == "downloaded" else result.reason


def select_fulltexts(run: PaperSelectionRun) -> None:
    """全部摘要已完成后一次冻结最多九篇；未选项保留而不排除。"""
    if run.phase != "abstracts":
        return
    if any(i.state in {"awaiting_abstract", "abstract_failed"} for i in run.items):
        return
    priority = {j.casefold() for j in run.authority_journals}

    def order(item: PaperSelectionItem) -> tuple[int, int, str]:
        journal = item.journal.casefold()
        preprint = item.source_id in {"arxiv", "biorxiv", "medrxiv"} or any(
            name in journal for name in ("biorxiv", "medrxiv", "arxiv", "research square")
        )
        authority = 0 if journal in priority else 2 if preprint else 1 if journal else 3
        return authority, -item.relevance_score, item.request.evidence[0].id

    eligible = sorted((i for i in run.items if i.state == "eligible"), key=order)
    for index, item in enumerate(eligible, 1):
        item.rank = index
        item.state = "shortlisted" if index <= run.shortlist_limit else "deferred"
    run.phase = "fulltexts"
