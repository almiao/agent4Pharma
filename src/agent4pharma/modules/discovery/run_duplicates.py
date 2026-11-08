"""发现批次的重复展示规则：区分既有评估复用和同资料多来源，不改写处理状态。"""

from agent4pharma.modules.discovery.run_records import RunArticle


def annotate_run_duplicates(articles: list[RunArticle]) -> None:
    """按明确资料身份标注；已归档待重评、更新版本和技术失败不建议直接忽略。"""
    primary: dict[str, str] = {}
    for article in articles:
        if article.state == "already_seen":
            article.duplicate_kind = "already_assessed"
            article.duplicate_reason = "该来源版本已有同策略评估，已复用；无需重复处理。"
            article.ignore_recommended = True
        elif article.material_id in primary and article.member_kind != "version_update":
            article.duplicate_kind = "same_material"
            article.duplicate_of = primary[article.material_id]
            article.duplicate_reason = "本批已有同一资料的另一来源项，可合并阅读；不同出处或版本仍需保留。"
        primary.setdefault(article.material_id, article.id)
