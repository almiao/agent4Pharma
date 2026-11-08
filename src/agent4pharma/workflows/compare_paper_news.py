"""固定本地前十的整体新闻比较、有限重试及人工下载清单冻结。"""

from agent4pharma.modules.discovery.news_preferences import match_priority_institutions
from agent4pharma.modules.discovery.news_selection import (
    NewsComparisonModel,
    NewsComparisonPaper,
    NewsComparisonRequest,
    NewsComparisonRequestError,
    NewsComparisonValidationError,
    validate_news_comparison,
)
from agent4pharma.modules.discovery.paper_selection import PaperSelectionRun, PaperSelectionStore
from agent4pharma.shared.clock import utcnow


def compare_paper_news(
    run: PaperSelectionRun, store: PaperSelectionStore, model: NewsComparisonModel | None
) -> None:
    """一次比较完整冻结池，失败保存断点且不扩池；两次耗尽后需显式重试。

    调用方持工作锁，不在数据库事务内调用模型。成功零入选也完成摘要阶段。
    比较输入和输出归运行所有，保存后续跑不重复调用模型，不生成正式候选。
    """
    pool = sorted(
        (i for i in run.items if i.local_rank and i.local_rank <= run.review_pool_limit),
        key=lambda i: i.local_rank,
    )
    if not pool:
        run.phase = "fulltexts"
        store.save(run)
        return
    if run.comparison_attempts >= 2 and run.comparison_result is None:
        return
    if model is None and run.comparison_result is None:
        raise ValueError("前十已保存，商业新闻比较未配置模型")
    if run.comparison_request is None:
        first = pool[0].request
        if any(
            (i.request.model, i.request.model_endpoint, i.request.model_max_tokens)
            != (first.model, first.model_endpoint, first.model_max_tokens)
            for i in pool
        ):
            raise ValueError("新闻比较池的商业模型身份不一致")
        priority = {j.casefold() for j in run.authority_journals}
        run.comparison_request = NewsComparisonRequest(
            version=run.news_selection_version,
            instructions=run.comparison_instructions,
            model=first.model,
            endpoint=first.model_endpoint,
            max_tokens=first.model_max_tokens,
            thinking_mode=run.comparison_thinking_mode,
            limit=min(5, run.shortlist_limit),
            papers=[
                NewsComparisonPaper(
                    record_id=i.request.evidence[0].id,
                    title=i.request.evidence[0].title,
                    text=(
                        i.abstract_evidence.text
                        if i.abstract_evidence.status == "available"
                        else i.request.evidence[0].text
                    ),
                    journal=i.journal,
                    priority_journal=i.journal.casefold() in priority,
                    local_rank=i.local_rank,
                    affiliations=i.affiliations,
                    priority_institutions=match_priority_institutions(
                        i.affiliations, run.institution_preferences
                    ),
                )
                for i in pool
            ],
        )
    if run.comparison_result is None:
        run.comparison_attempts += 1
        run.comparison_error = ""
        run.processing_stage, run.activity_updated_at = "commercial_review", utcnow()
        store.save(run)
        try:
            result = model.compare(run.comparison_request)
            validate_news_comparison(run.comparison_request, result)
        except Exception as error:
            run.comparison_error = (
                str(error)
                if isinstance(error, (NewsComparisonValidationError, NewsComparisonRequestError))
                else "商业比较请求或协议失败（" + type(error).__name__ + "）"
            )
            for item in pool:
                item.state, item.reason = "review_failed", run.comparison_error
            store.save(run)
            raise
        run.comparison_result = result
        run.comparison_error = ""
        store.save(run)
    result = run.comparison_result
    papers = {p.record_id: p for p in run.comparison_request.papers}
    # 保留模型原始顺序供审阅；机构、重点期刊的明确优先级不能被自由文本排序覆盖。
    selected = sorted(
        result.selected_ids,
        key=lambda identity: (
            not bool(papers[identity].priority_institutions),
            not papers[identity].priority_journal,
        ),
    )
    ranks = {identity: rank for rank, identity in enumerate(selected, 1)}
    cards = {card.record_id: card for card in result.cards}
    for item in pool:
        identity = item.request.evidence[0].id
        card = cards[identity]
        item.rank = ranks.get(identity)
        item.relevance_score = card.relevance
        item.state = (
            "shortlisted" if item.rank else "review_excluded" if card.verdict == "exclude" else "deferred"
        )
        if item.selection_reason:
            # 已保存的展示快照保持原文；新制作只读取结构化卡片，不继承旧核实问题。
            item.reason = item.selection_reason
            continue
        item.selection_reason = "\n".join(
            filter(
                None,
                [
                    "新闻点：" + card.news_hook if card.news_hook else "",
                    card.reason,
                    "依据：" + card.evidence_quote if card.evidence_quote else "",
                    "机构依据：" + card.institution_quote if card.institution_quote else "",
                ],
            )
        )
        item.reason = item.selection_reason
        if card.verdict == "recommend" and item.rank is None:
            item.selection_reason = (
                "模型认为有选题价值，但未列入本次下载清单，容量延期。\n" + item.selection_reason
            )
            item.reason = item.selection_reason
    run.phase = "fulltexts"
    store.save(run)
