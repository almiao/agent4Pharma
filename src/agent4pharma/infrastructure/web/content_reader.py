"""内容补读适配器；通用网页解析不提供全文完整性认证。"""

import hashlib

from agent4pharma.infrastructure.web.article_parser import parse_document
from agent4pharma.infrastructure.web.public_fetch import PublicPageFetcher
from agent4pharma.modules.content.draft_contracts import ResearchEvidence
from agent4pharma.shared.clock import utcnow


def read_content_source(url: str) -> ResearchEvidence:
    """获取程序已验证在任务范围内的公开 URL，失败明确抛错，不运行页面命令。"""
    page = parse_document(PublicPageFetcher().fetch(url), "content", "page", url)
    if page.status != "fetched" or not page.text.strip():
        raise ValueError("未取得可阅读原文；PDF、付费页或截断不能冒充全文")
    identity = "page-" + hashlib.sha256((url + page.text).encode()).hexdigest()[:24]
    return ResearchEvidence(
        id=identity,
        title=page.title,
        text=page.text,
        url=page.url,
        origin="primary_page",
        fulltext=False,
        read_scope=f"公开网页正文提取，获取于{utcnow()}；不认证全文完整性或独立验证。",
    )
