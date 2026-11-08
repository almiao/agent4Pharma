"""新闻定稿的有限退稿边界；结构校验不替代编辑的语义判断。"""

from collections.abc import Mapping, Sequence

from agent4pharma.modules.review.review_contracts import ReviewSubmission


def validate_news_acceptance(
    result: ReviewSubmission,
    draft_text: str,
    sources: Mapping[str, str],
    requirements: Sequence[str],
) -> None:
    """拒绝无定位必改和将风格建议作为退稿理由，不自动改写为通过。"""
    for issue in result.issues:
        if issue.severity == "suggestion":
            continue
        if issue.category == "factual_error":
            if not issue.draft_quote.strip() or issue.draft_quote not in draft_text:
                raise ValueError(f"{issue.id}: 事实必改须引用当前稿件实际存在的问题句，不能放大改写")
            source = sources.get(issue.evidence_id, "")
            if not issue.source_quote.strip() or issue.source_quote not in source:
                raise ValueError(f"{issue.id}: 事实必改须给出绑定来源的连续纠正依据")
        elif issue.category == "requirement_gap":
            if not issue.requirement.strip() or issue.requirement not in requirements:
                raise ValueError(f"{issue.id}: 要求缺失须引用当前任务书的准确报道角度或关键事实边界")
        else:
            raise ValueError(f"{issue.id}: 只有事实错误或明确要求缺失可列必改，措辞与过渡只能建议")
