"""根据实际链接和访问状态构造引用链；启发式结果始终保持候选标识。"""

from urllib.parse import urlsplit

from agent4pharma.modules.maintenance.identifiers import normalized_url
from agent4pharma.modules.maintenance.models import (
    LocalMaterialMatch,
    OriginalCandidate,
    PageEvidence,
    ReferenceLink,
    SourceSuggestion,
)
from agent4pharma.modules.maintenance.repository import MaterialLookup
from agent4pharma.modules.sources.management import SourceCatalog


def reference_score(link: ReferenceLink) -> int:
    """给实际页面引用排序，不把排序分数解释成原始来源可信度。"""
    host = urlsplit(link.url).hostname or ""
    score = 100 if link.kind == "doi" or host.endswith("doi.org") else 0
    if any(
        host == domain or host.endswith("." + domain)
        for domain in (
            "pubmed.ncbi.nlm.nih.gov",
            "arxiv.org",
            "biorxiv.org",
            "medrxiv.org",
        )
    ):
        score += 60
    if link.kind == "index_result":
        score += 50
    if any(
        term in link.label.casefold()
        for term in (
            "original",
            "paper",
            "study",
            "research",
            "source",
            "press release",
            "原文",
            "论文",
            "研究",
        )
    ):
        score += 20
    return score


def candidate_links(pages: list[PageEvidence]) -> list[tuple[PageEvidence, ReferenceLink]]:
    """从已读页面挑选潜在研究/原始报道链接，保留其父证据。"""
    values = []
    seen = set()
    for page in pages:
        for link in sorted(page.links, key=reference_score, reverse=True):
            key = normalized_url(link.url)
            if key not in seen and reference_score(link) > 0:
                values.append((page, link))
                seen.add(key)
    return values[:20]


def attach_candidates(pages: list[PageEvidence], lookup: MaterialLookup) -> list[OriginalCandidate]:
    """为引用链补充实际读取状态和本地匹配，不能把相关链接自动确认为原始研究。"""
    result = []
    for page, link in candidate_links(pages):
        fetched = next(
            (
                p
                for p in pages
                if normalized_url(link.url)
                in {
                    normalized_url(p.requested_url),
                    normalized_url(p.url),
                }
            ),
            None,
        )
        status = (
            "page_read_candidate"
            if fetched and fetched.status == "fetched"
            else "access_failed_candidate"
            if fetched
            else "linked_not_fetched"
        )
        result.append(
            OriginalCandidate(
                url=link.url,
                evidence_id=page.id,
                reason="实际页面中的引用/标识；需判断是否为本报道的原始来源",
                quote=link.label[:1000],
                status=status,
                local_matches=lookup.find_material(link.url),
            )
        )
    return result


def source_suggestions(pages: list[PageEvidence], catalog: SourceCatalog) -> list[SourceSuggestion]:
    """提供经访问的站点/Feed 候选，不因一篇文章可访问就宣称能自动发现全站文章。"""
    result, seen = [], set()
    for page in pages:
        host = urlsplit(page.url).hostname
        if not host or host in seen or page.status != "fetched":
            continue
        seen.add(host)
        feeds = [link for link in page.links if link.kind == "feed"]
        known = catalog.list_sources(host, 100)
        result.append(
            SourceSuggestion(
                url=feeds[0].url if feeds else urlsplit(page.url).scheme + "://" + urlsplit(page.url).netloc,
                name=host,
                suitability="candidate" if feeds else "needs_review",
                evidence_id=page.id,
                in_catalog=any(
                    host in {urlsplit(s.homepage_url).hostname, urlsplit(s.endpoint_url).hostname}
                    for s in known
                ),
                reason="页面声明了 RSS/Atom，仍需探查 Feed 发现范围与连接器"
                if feeds
                else "已读取此站文章；尚未验证列表/API/Feed，不代表适合作为自动数据源",
            )
        )
    return result


def unique_matches(values: list[LocalMaterialMatch]) -> list[LocalMaterialMatch]:
    """按库和资料身份去重，避免一篇文献多个引用被重复计数。"""
    return list({(value.database, value.material_id): value for value in values}.values())
