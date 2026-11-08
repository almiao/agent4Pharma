"""已人工确认 RSS/Atom 入口的有限采集；未知日期保持未知，不伪造历史窗口覆盖。"""

import hashlib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Literal
from urllib.parse import urljoin
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from agent4pharma.infrastructure.connectors.rss_affiliations import (
    AFFILIATION_EXTRACTION_VERSION,
    extract_author_affiliations,
)
from agent4pharma.infrastructure.connectors.rss_signals import extract_signals
from agent4pharma.infrastructure.connectors.source_abstracts import feed_abstract_metadata
from agent4pharma.infrastructure.web.public_fetch import PublicPageFetcher
from agent4pharma.modules.collection.errors import SourceError
from agent4pharma.modules.materials.collection_records import CollectedRecord, CollectionPage, SourceResponse
from agent4pharma.modules.sources.collection_plans import RssExtractionProfile

FEED_PARSER_VERSION = "rss-v4"


def _entry_doi(entry: ElementTree.Element, explicit_doi: str) -> str:
    # 保留明确 DOI 字段的既有优先级；dc:identifier 也可能是 ISSN/PII，必须逐值验证。
    if explicit_doi:
        return explicit_doi
    for node in entry:
        if node.tag != "{http://purl.org/dc/elements/1.1/}identifier":
            continue
        value = "".join(node.itertext()).strip()
        value = re.sub(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)", "", value, flags=re.I)
        if re.fullmatch(r"10\.\d{4,9}/\S+", value):
            return value
    return ""


class RssPageFetcher(PublicPageFetcher):
    """RSS 请求明确接受 XML；保留公开网页读取的地址校验、超时及访问边界。"""

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            **PublicPageFetcher._headers(),
            "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.5",
        }


def parse_feed(
    body: bytes, url: str, source: str, profile: RssExtractionProfile | None = None
) -> list[CollectedRecord]:
    """解析有限 RSS/Atom 原文，拒绝实体声明及非 Feed 文档。"""
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise ValueError("Feed 不允许实体声明")
    root = ElementTree.fromstring(body)

    def tag(name: str) -> str:
        return name.rsplit("}", 1)[-1]

    if tag(root.tag) not in {"rss", "feed", "RDF"}:
        raise ValueError("不是 RSS/Atom 文档")
    records = []
    for entry in [e for e in root.iter() if tag(e.tag) in {"item", "entry"}][:200]:
        fields = {tag(e.tag): "".join(e.itertext()).strip() for e in entry}
        links = [
            e.attrib.get("href", "")
            for e in entry
            if tag(e.tag) == "link" and e.attrib.get("rel", "alternate") == "alternate"
        ]
        link = fields.get("link", "") or next(iter(links), "")
        if not link:
            continue
        link = urljoin(url, link)
        date_field = next((key for key in ("published", "pubDate", "date", "updated") if fields.get(key)), "")
        timestamp = fields.get(date_field) if date_field else None
        published = None
        if timestamp:
            try:
                point = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                try:
                    point = parsedate_to_datetime(timestamp)
                except (ValueError, TypeError):
                    point = None
            if point is not None and point.tzinfo is not None:
                published = point.astimezone(timezone.utc).isoformat()
        identifier = fields.get("guid") or fields.get("id") or link
        abstract_field = next(
            (key for key in ("description", "summary", "content", "encoded") if fields.get(key)), ""
        )
        summary = fields.get("description") or fields.get("summary") or fields.get("content", "")
        if not summary and fields.get("encoded"):
            # RSS 1.0 的 content:encoded 是 Nature 简介的实际载体，不执行其中的 HTML。
            soup = BeautifulSoup(fields["encoded"], "html.parser")
            for node in soup.select("script, style"):
                node.decompose()
            summary = soup.get_text(" ", strip=True)
        authors = [
            " ".join(e.itertext()).strip()
            for e in entry
            if tag(e.tag) == "creator" and " ".join(e.itertext()).strip()
        ]
        for author in entry:
            if tag(author.tag) == "author":
                name = next((" ".join(n.itertext()).strip() for n in author if tag(n.tag) == "name"), "")
                if name:
                    authors.append(name)
        affiliations = extract_author_affiliations(entry)
        doi = _entry_doi(entry, fields.get("doi", ""))
        records.append(
            CollectedRecord(
                source,
                hashlib.sha256(identifier.encode()).hexdigest(),
                fields.get("title", link)[:1000],
                summary[:60000],
                authors=list(dict.fromkeys(authors)),
                identifiers={"doi": doi} if doi else {},
                url=link,
                published=published,
                metadata={
                    "feed_url": url,
                    "date_raw": timestamp,
                    "date_field": date_field,
                    "coverage": "current_feed_only",
                    "feed_parser_version": FEED_PARSER_VERSION,
                    "affiliations_version": AFFILIATION_EXTRACTION_VERSION,
                    "affiliations": [e.text for e in affiliations],
                    "affiliation_evidence": [e.model_dump(mode="json") for e in affiliations],
                    **feed_abstract_metadata(summary[:60000], url, abstract_field),
                    **({"rss_signal": extract_signals(entry, profile)} if profile else {}),
                },
            )
        )
    return records


class RssFeedConnector:
    """单页当前 Feed 快照；不宣称回溯超过 Feed 保留范围。"""

    def __init__(
        self,
        source: str,
        url: str,
        profile: RssExtractionProfile | None = None,
        *,
        date_policy: Literal["publication_window", "current_feed"] = "publication_window",
    ):
        if date_policy not in {"publication_window", "current_feed"}:
            raise ValueError("未知 RSS 日期策略")
        self.source, self.url, self.fetcher = source, url, RssPageFetcher(max_bytes=4_000_000)
        self.profile = profile
        self.date_policy = date_policy

    def page(self, start: datetime, end: datetime, query: str, cursor: dict | None) -> CollectionPage:
        """按冻结日期策略读取当前 Feed；原始发表时间不因批次日期改变。"""
        try:
            document = self.fetcher.fetch(self.url)
            if document.http_status != 200:
                raise SourceError(f"RSS HTTP {document.http_status}：上游未返回可读取的 Feed")
            records = parse_feed(document.body, document.url, self.source, self.profile)
        except (ValueError, OSError, ElementTree.ParseError) as exc:
            raise SourceError("RSS 读取/解析失败：" + type(exc).__name__) from None
        selected = [
            r
            for r in records
            if self.date_policy == "current_feed"
            or not r.published
            or start <= datetime.fromisoformat(r.published) < end
        ]
        return CollectionPage(
            selected,
            None,
            [SourceResponse(document.body, document.url, document.content_type)],
            len(records),
            "current_feed_only; "
            + ("publication_window_not_applied; " if self.date_policy == "current_feed" else "")
            + "unknown_dates_retained; no_historical_coverage_claim",
        )
