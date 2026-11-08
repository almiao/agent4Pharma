"""本地主题判别与新闻价值前十，商业整体比较最多五篇供人工下载。"""

import math
from collections.abc import Callable

from agent4pharma.modules.discovery.intelligence_contracts import IntelligenceEvidence
from agent4pharma.modules.discovery.news_preferences import match_priority_institutions
from agent4pharma.modules.discovery.news_selection import NEWS_SELECTION_VERSION, NewsComparisonModel
from agent4pharma.modules.discovery.paper_selection import (
    PaperSelectionItem,
    PaperSelectionRun,
    PaperSelectionStore,
)
from agent4pharma.modules.discovery.triage_contracts import LocalPaperDecisionModel, SignalField, TriageInput
from agent4pharma.shared.clock import utcnow
from agent4pharma.workflows.compare_paper_news import compare_paper_news


def _input(item: PaperSelectionItem, run: PaperSelectionRun) -> TriageInput:
    primary = item.request.evidence[0]
    assert item.abstract_evidence is not None
    abstract = item.abstract_evidence.text.strip() if item.abstract_evidence.status == "available" else ""
    # 与来源预选采用同一摘录上限，不能把缺摘要媒体的整篇正文送入8K上下文模型。
    # 原证据保持完整；显式截断标记让判别器知道本次可见范围。
    fields = (
        [SignalField(field="abstract", text=abstract[:12000], truncated=len(abstract) > 12000)]
        if abstract
        else []
    )
    if not abstract and primary.text.strip() and primary.text.strip() != primary.title.strip():
        fields.append(
            SignalField(field="source_text", text=primary.text[:12000], truncated=len(primary.text) > 12000)
        )
    information_status = "abstract" if abstract else "source_text" if fields else "title_only"
    fields.extend(SignalField(field="affiliation", text=text) for text in item.affiliations)
    fields.extend(
        SignalField(field="priority_institution", text=name)
        for name in match_priority_institutions(item.affiliations, run.institution_preferences)
    )
    return TriageInput(
        material_ref=item.request.material_ref,
        source_record_id=primary.id,
        source_id=item.source_id,
        title=primary.title,
        journal=item.journal,
        url=primary.url,
        fields=fields,
        information_status=information_status,
        extraction_version="local-abstract-v5",
    )


def _review_pool(run: PaperSelectionRun) -> None:
    if any(i.state in {"awaiting_abstract", "local_failed"} for i in run.items):
        return
    eligible = [i for i in run.items if i.state == "eligible"]
    if any(i.local_margin is None for i in eligible):
        return
    priority = {j.casefold() for j in run.authority_journals}

    # 主题只作门槛；机构、期刊依次优先，同档比较新闻价值，不用分类概率。
    def order(item: PaperSelectionItem) -> tuple[int, int, float, str]:
        journal = item.journal.casefold()
        preprint = item.source_id in {"arxiv", "biorxiv", "medrxiv"}
        group = 0 if journal in priority else 2 if preprint else 1 if journal else 3
        if run.news_selection_version == NEWS_SELECTION_VERSION:
            group = 0 if journal in priority else 1
        known = bool(match_priority_institutions(item.affiliations, run.institution_preferences))
        return (0 if known else 1), group, -item.local_margin, item.request.evidence[0].id

    for rank, item in enumerate(sorted(eligible, key=order), 1):
        item.local_rank = rank
        item.state = "review_pending" if rank <= run.review_pool_limit else "deferred"
    run.phase = "commercial_review"


def screen_local_papers(
    run: PaperSelectionRun,
    store: PaperSelectionStore,
    model: LocalPaperDecisionModel,
    read_abstracts: Callable[[list[PaperSelectionItem]], list[IntelligenceEvidence]],
    *,
    abstract_budget: int = 500,
    review_budget: int = 10,
    news_model: NewsComparisonModel | None = None,
) -> None:
    """每16篇保存断点；无摘要按现有资料正常判断，技术失败不排除，无自动下载或全文分析。

    商业复核只允许已冻结前十；复核失败阻止完成名单，最多尝试两次，结果不产生正式候选。
    调用方持工作锁，模型与网络不在数据库事务内。暂停运行不消费资源。
    """
    if (
        run.selection_method != "local_decider_v1"
        or run.local_policy is None
        or run.missing_abstract_policy != "best_available"
        or run.news_selection_version != NEWS_SELECTION_VERSION
    ):
        raise ValueError("没有冻结本地摘要策略")
    if not 0 <= abstract_budget <= 500 or not 0 <= review_budget <= 10:
        raise ValueError("本地每轮最多500篇，商业复核最多10篇")
    if run.paused or run.phase == "fulltexts":
        return
    if run.phase == "abstracts":
        pending = [
            i
            for i in run.items
            if i.state in {"awaiting_abstract", "local_failed"}
            or i.state == "eligible"
            and i.local_margin is None
        ]
        pending.sort(key=lambda item: item.local_decision is not None)
        pending = pending[:abstract_budget]
        for start in range(0, len(pending), 16):
            chunk = pending[start : start + 16]
            run.processing_stage, run.activity_updated_at = "local_abstracts", utcnow()
            store.save(run)
            try:
                missing = [
                    i
                    for i in chunk
                    if i.abstract_evidence is None or i.abstract_evidence.abstract_status == "error"
                ]
                if missing:
                    abstracts = read_abstracts(missing)
                    if len(abstracts) != len(missing):
                        raise ValueError("摘要返回数量错误")
                    for item, evidence in zip(missing, abstracts, strict=True):
                        if evidence.id != item.request.evidence[0].id:
                            raise ValueError("摘要身份不匹配")
                        item.abstract_evidence = evidence
                if any(i.abstract_evidence.abstract_status == "error" for i in chunk):
                    raise RuntimeError("摘要上游获取技术失败")
                classify = [i for i in chunk if i.local_decision is None]
                if classify:
                    decisions = model.classify([_input(i, run) for i in classify], run.local_policy)
                    if len(decisions) != len(classify):
                        raise ValueError("本地判别数量错误")
                    for item, decision in zip(classify, decisions, strict=True):
                        item.local_decision = decision
                rank = []
                for item in chunk:
                    result = item.local_decision
                    passed = result.probabilities.analyze > result.probabilities.exclude
                    item.state = "eligible" if passed else "abstract_excluded"
                    item.reason = "本地摘要判断：通过" if passed else "本地摘要判断：不进入本轮全文队列"
                    if passed and item.local_margin is None:
                        rank.append(item)
                if rank:
                    run.processing_stage, run.activity_updated_at = "local_ranking", utcnow()
                    store.save(run)
                    margins = model.rank([_input(i, run) for i in rank], run.ranking_instructions)
                    if len(margins) != len(rank) or not all(math.isfinite(m) for m in margins):
                        raise ValueError("本地排序分值非法")
                    for item, margin in zip(rank, margins, strict=True):
                        item.local_margin = margin
            except Exception:
                for item in chunk:
                    if item.state != "abstract_excluded":
                        item.state, item.reason = "local_failed", "本地摘要判别或排序技术失败，保留断点"
                raise
            finally:
                run.activity_updated_at = utcnow()
                store.save(run)
        _review_pool(run)
        store.save(run)
    if run.phase != "commercial_review" or review_budget == 0:
        return
    pool_size = sum(bool(i.local_rank and i.local_rank <= run.review_pool_limit) for i in run.items)
    if review_budget < pool_size:
        raise ValueError("新闻比较必须一次覆盖冻结前十，请提供足够的复核预算")
    compare_paper_news(run, store, news_model)
