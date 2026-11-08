"""将工作台共享主题编译为来源查询预览；不自动执行或改写采集配置。"""

from agent4pharma.modules.discovery.screening_policy import ScreeningPolicy

# PubMed 的二手文献类型不进发现流程：综述/社论/评论/通讯/新闻多为转述，不是本轮要发现的新研究。
# 2026-10-31 用户确认排除；实测 2026-10-30 单日 AI × Biology/Pharma 命中由 257 条降到 196 条。
PUBMED_EXCLUDED_TYPES = ("Review", "Editorial", "Comment", "Letter", "News")


def render_queries(policy: ScreeningPolicy) -> dict[str, str]:
    """生成英文标题/摘要检索式，日期仍由来源连接器单独附加。"""
    methods = [t for t in policy.agent_terms + policy.model_terms if t.isascii()]
    tasks = [t for t in policy.drug_terms if t.isascii()]

    def phrase(term: str) -> str:
        return term.replace('"', " ").replace("\n", " ").strip()

    def pubmed(terms: list[str]) -> str:
        return "(" + " OR ".join(f'"{phrase(t)}"[Title/Abstract]' for t in terms) + ")"

    def arxiv(terms: list[str]) -> str:
        return "(" + " OR ".join(f'(ti:"{phrase(t)}" OR abs:"{phrase(t)}")' for t in terms) + ")"

    # 主题分支整体加括号后再 NOT，避免 NOT 只作用于最后一个 OR 分支。
    exclusions = (
        " NOT (" + " OR ".join(f'"{phrase(t)}"[Publication Type]' for t in PUBMED_EXCLUDED_TYPES) + ")"
    )

    return {
        "pubmed": f"({pubmed(methods)} AND {pubmed(tasks)}){exclusions}",
        "arxiv": arxiv(methods) + " AND " + arxiv(tasks),
        "rxiv": "日期范围元数据 → 本地 Decider 二分类；关键词不参与资料过滤",
    }
