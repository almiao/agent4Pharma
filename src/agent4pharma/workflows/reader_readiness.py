"""阅读通过只对准确稿件与任务书有效；事实通过不补造阅读结论。"""

from agent4pharma.workflows.production_dependencies import ProductionContext


def require_reader_ready(context: ProductionContext) -> None:
    """新策略进入事实核验和制包前，检查最近阅读报告绑定准确版本。"""
    draft, story = context.draft, context.story
    reports = context.reader_reviews
    if draft is None or story is None or not reports:
        raise ValueError("缺少准确稿件的阅读品质结论")
    report = reports[-1]
    if (
        report.submission.decision != "ready"
        or report.draft_id != draft.id
        or report.draft_hash != draft.content_hash
        or report.brief_version != story.brief_version
    ):
        raise ValueError("阅读品质结论未通过或已经失效，须复核当前稿件")
