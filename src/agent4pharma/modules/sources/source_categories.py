"""来源展示类别；只用于筛选，不参与主题判断或改写历史采集身份。"""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

SourceCategory = Literal["journal", "preprint", "news", "announcement", "other", "unknown"]


@lru_cache(maxsize=8192)
def source_category(
    source_id: str, *, source_kind: str = "", material_type: str = "", url: str = "", journal: str = ""
) -> SourceCategory:
    """依据明确来源身份/类型分类；不从标题关键词猜测，不确认的保留 unknown。"""
    host = (urlsplit(url).hostname or "").lower()
    if (
        source_id in {"arxiv", "biorxiv", "medrxiv"}
        or any(
            host == domain or host.endswith("." + domain)
            for domain in ("arxiv.org", "biorxiv.org", "medrxiv.org")
        )
        or source_kind in {"preprint_server", "preprint"}
    ):
        return "preprint"
    if material_type in {
        "announcement",
        "press_release",
        "company_announcement",
        "financial_filing",
    } or source_kind in {
        "announcement",
        "press_release",
        "press_release_platform",
        "filing_registry",
    }:
        return "announcement"
    if source_kind in {"news_site", "news_media", "news_section"} or material_type == "news_report":
        return "news"
    if source_kind in {"journal_site", "journal"} or source_id == "pubmed" or journal:
        return "journal"
    if source_kind in {
        "blog",
        "blog_site",
        "social_account",
        "website",
        "publisher_site",
        "identifier_service",
        "conference_site",
        "repository_platform",
    } or material_type in {
        "blog_post",
        "newsletter",
        "editorial_article",
    }:
        return "other"
    return "unknown"
