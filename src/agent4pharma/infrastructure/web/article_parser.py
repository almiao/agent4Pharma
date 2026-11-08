"""从已取得页面提取标题、正文、实际外链和引用标识，不把引用推定为原始研究。"""

import json
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

from agent4pharma.infrastructure.connectors.rss_feed import parse_feed
from agent4pharma.modules.maintenance.identifiers import identifiers_from_text
from agent4pharma.modules.maintenance.models import PageEvidence, ReferenceLink
from agent4pharma.modules.maintenance.repository import FetchedDocument
from agent4pharma.shared.clock import utcnow


class ArticleParser(HTMLParser):
    """解析有限 HTML，不执行脚本；保留锚文本和声明性元数据作为引用证据。"""

    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base, self.title, self.canonical = base, "", ""
        self.texts: list[str] = []
        self.links: list[ReferenceLink] = []
        self.identifier_texts: list[str] = []
        self.venue_name = ""
        self._title = False
        self._ignored = 0
        self._anchor: tuple[str, list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """提取实际 href、canonical、RSS 及 citation 元数据。"""
        values = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self._ignored += 1
        if tag == "title":
            self._title = True
        if tag == "a" and values.get("href"):
            self._anchor = (urljoin(self.base, values["href"]), [])
        if tag == "link" and values.get("href"):
            url = urljoin(self.base, values["href"])
            rel = values.get("rel", "") or ""
            if "canonical" in rel:
                self.canonical = url
                self.links.append(ReferenceLink(url=url, label="canonical", kind="canonical"))
            elif "rss" in (values.get("type", "") or "") or "atom" in (values.get("type", "") or ""):
                self.links.append(ReferenceLink(url=url, label="RSS/Atom", kind="feed"))
        if tag == "meta":
            name, content = values.get("name", "") or "", values.get("content", "") or ""
            if name in {"citation_doi", "dc.identifier", "DC.Identifier"}:
                self.identifier_texts.append(content)
            if name == "citation_pmid":
                self.identifier_texts.append("pmid:" + content)
            if name == "citation_title" and content:
                self.title = content
            if name == "citation_journal_title":
                self.venue_name = content

    def handle_endtag(self, tag: str) -> None:
        """结束锚点时保存可审阅的真实链接标签。"""
        if tag in {"script", "style", "noscript"}:
            self._ignored = max(0, self._ignored - 1)
        if tag == "title":
            self._title = False
        if tag == "a" and self._anchor:
            url, labels = self._anchor
            if urlsplit(url).scheme in {"http", "https"}:
                self.links.append(ReferenceLink(url=url, label=" ".join(labels)[:300]))
            self._anchor = None

    def handle_data(self, data: str) -> None:
        """收集可见文本，忽略脚本，限制后续模型上下文长度。"""
        if self._ignored:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._title:
            self.title += text
        if self._anchor:
            self._anchor[1].append(text)
        self.texts.append(text)


def parse_document(
    document: FetchedDocument, job_id: str, evidence_id: str, requested_url: str
) -> PageEvidence:
    """把 HTTP 成败与已解析内容分开保存；二进制/PDF 不伪装成已读全文。"""
    page = PageEvidence(
        id=evidence_id,
        job_id=job_id,
        requested_url=requested_url,
        url=document.url,
        status="fetched" if document.http_status == 200 else "http_error",
        http_status=document.http_status,
        retrieved_at=utcnow(),
    )
    if document.http_status != 200:
        page.error = f"HTTP {document.http_status}；未取得可核对正文"
        return page
    if not any(value in document.content_type for value in ("html", "text", "json", "xml")):
        return page.model_copy(
            update={"status": "unsupported_type", "error": "未解析此格式，不能声称已读正文"}
        )
    content = document.body.decode("utf-8", errors="replace")
    if "xml" in document.content_type or content.lstrip().startswith(("<?xml", "<rss", "<feed")):
        try:
            entries = parse_feed(document.body, document.url, "probe")
        except (ValueError, ElementTree.ParseError):
            entries = []
        if entries:
            return page.model_copy(
                update={
                    "title": "RSS/Atom 当前列表",
                    "text": "\n".join(r.title + " " + r.abstract for r in entries)[:60000],
                    "feed_entries": [
                        ReferenceLink(url=r.url, label=r.title, kind="feed_entry") for r in entries
                    ],
                    "links": [ReferenceLink(url=r.url, label=r.title, kind="feed_entry") for r in entries],
                }
            )
    if "json" in document.content_type:
        return _parse_crossref(page, content)
    parser = ArticleParser(document.url)
    parser.feed(content)
    text = "\n".join(parser.texts)
    identifiers = identifiers_from_text("\n".join([document.url, text, *parser.identifier_texts]))
    links = parser.links
    for identity in identifiers:
        if identity.startswith("doi:"):
            links.append(ReferenceLink(url="https://doi.org/" + identity[4:], label=identity, kind="doi"))
    unique = {link.url: link for link in links}
    return page.model_copy(
        update={
            "title": parser.title[:1000],
            "text": text[:60000],
            "links": list(unique.values())[:150],
            "identifiers": identifiers[:100],
            "canonical_url": parser.canonical,
            "venue_name": parser.venue_name,
        }
    )


def _parse_crossref(page: PageEvidence, content: str) -> PageEvidence:
    try:
        message = json.loads(content).get("message", {})
        items = message.get("items", []) if isinstance(message, dict) else []
    except (ValueError, TypeError, AttributeError):
        return page.model_copy(update={"text": content[:30000]})
    if not items:
        return page.model_copy(update={"text": content[:30000]})
    links, lines = [], []
    for item in items[:10]:
        title = " ".join(item.get("title", []))
        if doi := item.get("DOI"):
            links.append(ReferenceLink(url="https://doi.org/" + doi, label=title, kind="index_result"))
        lines.append(title + " " + str(item.get("DOI", "")))
    return page.model_copy(
        update={"title": "Crossref 文献检索结果", "text": "\n".join(lines), "links": links}
    )
