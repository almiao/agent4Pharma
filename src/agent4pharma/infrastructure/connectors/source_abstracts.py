"""已知平台原生摘要的来源标记；普通 RSS 简介不能自动升级为真实摘要。"""

from urllib.parse import urlsplit

from agent4pharma.infrastructure.connectors.abstract_lookup import is_placeholder

FEED_PLATFORMS = {
    "rss.arxiv.org": "arxiv",
    "connect.biorxiv.org": "biorxiv",
    "connect.medrxiv.org": "medrxiv",
}


def source_abstract_metadata(text: str, source: str, field: str) -> dict[str, str]:
    """为已知原生摘要字段记录出处；空文本或占位文案不标记成功。"""
    if is_placeholder(text):
        return {}
    return {
        "abstract_status": "found",
        "abstract_source": source,
        "abstract_field": field,
        "abstract_annotation_version": "source-abstract-v1",
    }


def feed_abstract_metadata(text: str, url: str, field: str) -> dict[str, str]:
    """仅识别已验收的三类原生 Feed，其他期刊/媒体简介维持原语义。"""
    platform = FEED_PLATFORMS.get(urlsplit(url).hostname or "")
    return source_abstract_metadata(text, platform + "_rss", field) if platform else {}
