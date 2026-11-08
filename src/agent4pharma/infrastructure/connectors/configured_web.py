"""配置驱动 RSS/HTML 发现与详情读取；所有站点走同一有限采集状态机。"""

import hashlib
import time
from datetime import datetime
from xml.etree import ElementTree

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from agent4pharma.infrastructure.connectors.rss_feed import parse_feed
from agent4pharma.infrastructure.web.configured_extraction import (
    allowed_url,
    body_links,
    date_precision,
    extract_field,
    timestamp,
    validate_selectors,
)
from agent4pharma.modules.collection.acquisition_interfaces import SourceAccessPersistence
from agent4pharma.modules.collection.errors import SourceError
from agent4pharma.modules.maintenance.repository import FetchedDocument, PageFetcher
from agent4pharma.modules.materials.collection_records import (
    CollectedContent,
    CollectedRecord,
    CollectionPage,
    SourceResponse,
)
from agent4pharma.modules.sources.collection_plans import CollectionPlan, DiscoveryEntry

PARSER_VERSION = "configured-web-v2"


class DiscoveredArticle(BaseModel):
    """发现阶段冻结的文章线索，详情失败后沿原队列重试。"""

    url: str
    title: str = ""
    published: str | None = None


class WebCollectionCursor(BaseModel):
    """持久化候选列表和逐篇进度，已提交文章不因后续失败重新处理。"""

    candidates: list[DiscoveredArticle]
    index: int = 0
    seen_ids: list[str] = Field(default_factory=list)
    failed: list[DiscoveredArticle] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    discovery_windows: list["DiscoveryWindow"] = Field(default_factory=list)


class DiscoveryWindow(BaseModel):
    """实际发现页的排序和截断依据；不把队列长度当作扫描到的链接总量。"""

    url: str
    order: str
    available: int
    selected: int
    first_selected_date: str | None = None


class ConfiguredWebConnector:
    """按冻结配置读取当前有限窗口；不把当前列表伪装为历史业务日覆盖。

    首页保存发现快照，后续每页提交一篇详情与游标。失败保持上一页检查点，
    下次复用原队列。获取器由调用方注入，不在数据库事务中联网或等待。
    """

    def __init__(
        self, plan: CollectionPlan, fetcher: PageFetcher, access: SourceAccessPersistence | None = None
    ) -> None:
        validate_selectors(plan)
        self.plan, self.fetcher, self.access = plan, fetcher, access

    def page(self, start: datetime, end: datetime, query: str, cursor: dict | None) -> CollectionPage:
        """发现与详情各自推进有限一页；按新增/版本变化入批，不过滤成昨日发表。"""
        try:
            return (
                self._discover()
                if cursor is None
                else self._detail(WebCollectionCursor.model_validate(cursor))
            )
        except (ValueError, OSError, ElementTree.ParseError) as exc:
            raise SourceError("配置采集失败：" + type(exc).__name__) from None

    def read_article(self, url: str) -> CollectionPage:
        """显式单篇入口复用正式详情解析，不执行列表发现、不声称日期窗口召回。"""
        document = self._fetch(url)
        record = self._article(document, DiscoveredArticle(url=url))
        return CollectionPage(
            [record],
            None,
            [SourceResponse(document.body, document.url, document.content_type)],
            1,
            "explicit_single_article; no_source_window_coverage_claim",
        )

    def _fetch(self, url: str) -> FetchedDocument:
        allowed_url(url, url, self.plan)
        for attempt in range(2):
            if self.access:
                wait = self.access.reserve_request(self.plan.source_id, 1.0)
                if wait > 60:
                    raise SourceError("配置采集限流延期，等待后续运行恢复")
                time.sleep(wait)
            document = self.fetcher.fetch(url)
            allowed_url(document.url, url, self.plan)
            if self.access:
                self.access.archive_response(
                    document.body,
                    {"url": document.url, "http_status": document.http_status, "parser": PARSER_VERSION},
                )
            if document.http_status == 200:
                return document
            if attempt == 0 and (document.http_status == 429 or document.http_status >= 500):
                time.sleep(1)
                continue
            raise SourceError(f"配置入口 HTTP {document.http_status}，未取得可解析文章")
        raise SourceError("配置入口重试耗尽")

    def _discover(self) -> CollectionPage:
        candidates: dict[str, DiscoveredArticle] = {}
        raw, windows = [], []
        for entry in self.plan.entries:
            url, visited, count = entry.url, set(), 0
            for _ in range(entry.max_pages):
                if url in visited:
                    raise SourceError("发现入口分页未推进")
                visited.add(url)
                document = self._fetch(url)
                raw.append(SourceResponse(document.body, document.url, document.content_type))
                items, next_url = self._list_page(document, entry)
                if not items and not entry.allow_empty:
                    raise SourceError("发现规则未匹配文章，可能是模板变化或访问受阻")
                selected = items[: max(0, entry.max_links - count)]
                windows.append(
                    DiscoveryWindow(
                        url=document.url,
                        order=entry.order,
                        available=len(items),
                        selected=len(selected),
                        first_selected_date=selected[0].published if selected else None,
                    )
                )
                for item in selected:
                    candidates.setdefault(item.url, item)
                    count += 1
                if not next_url or count >= entry.max_links:
                    break
                url = next_url
        cursor = (
            WebCollectionCursor(candidates=list(candidates.values()), discovery_windows=windows)
            if candidates
            else None
        )
        return CollectionPage(
            [], cursor.model_dump() if cursor else None, raw, len(candidates), self._coverage()
        )

    def _list_page(
        self, document: FetchedDocument, entry: DiscoveryEntry
    ) -> tuple[list[DiscoveredArticle], str]:
        if entry.kind == "rss":
            records = parse_feed(document.body, document.url, self.plan.source_id)
            if entry.order == "newest_first":
                if any(not r.published for r in records):
                    raise SourceError("Feed 存在未知或无时区的逐篇时间，不能确认最新文章")
                records = sorted(records, key=lambda r: (r.published or "", r.url), reverse=True)
            return [
                DiscoveredArticle(
                    url=allowed_url(r.url, document.url, self.plan), title=r.title, published=r.published
                )
                for r in records
            ], ""
        soup = BeautifulSoup(document.body, "html.parser")
        items = {}
        for node in soup.select(entry.link_selector):
            href = node.get("href")
            if not isinstance(href, str) or not href:
                continue
            url = allowed_url(href, document.url, self.plan)
            items.setdefault(url, DiscoveredArticle(url=url, title=node.get_text(" ", strip=True)))
        next_node = soup.select_one(entry.next_selector) if entry.next_selector else None
        next_url = (
            allowed_url(str(next_node["href"]), document.url, self.plan)
            if next_node and next_node.get("href")
            else ""
        )
        return list(items.values()), next_url

    def _detail(self, cursor: WebCollectionCursor) -> CollectionPage:
        if not 0 <= cursor.index < len(cursor.candidates):
            raise ValueError("非法的文章队列检查点")
        candidate = cursor.candidates[cursor.index]
        records, raw = [], []
        try:
            document = self._fetch(candidate.url)
            raw.append(SourceResponse(document.body, document.url, document.content_type))
            record = self._article(document, candidate)
            records = [] if record.external_id in cursor.seen_ids else [record]
            cursor.seen_ids = list(dict.fromkeys([*cursor.seen_ids, record.external_id]))
        except (SourceError, ValueError, OSError) as exc:
            cursor.failed.append(candidate)
            cursor.errors.append(str(exc) if isinstance(exc, SourceError) else type(exc).__name__)
        cursor.index += 1
        deferred_error = None
        next_cursor = cursor.model_dump() if cursor.index < len(cursor.candidates) else None
        if next_cursor is None and cursor.failed:
            next_cursor = WebCollectionCursor(
                candidates=cursor.failed, seen_ids=cursor.seen_ids, discovery_windows=cursor.discovery_windows
            ).model_dump()
            deferred_error = f"{len(cursor.failed)} 篇详情待补抓：" + "; ".join(dict.fromkeys(cursor.errors))
        return CollectionPage(
            records,
            next_cursor,
            raw,
            len(cursor.candidates),
            self._coverage(),
            deferred_error=deferred_error,
        )

    def _article(self, document: FetchedDocument, candidate: DiscoveredArticle) -> CollectedRecord:
        soup = BeautifulSoup(document.body, "html.parser")
        rules = self.plan.article
        paywall = any(soup.select(selector) for selector in rules.paywall_selectors) or any(
            text.casefold() in soup.get_text(" ", strip=True).casefold() for text in rules.paywall_text
        )
        canonical = allowed_url(extract_field(soup, rules.canonical) or document.url, document.url, self.plan)
        external_id = extract_field(soup, rules.external_id) if rules.external_id.selectors else canonical
        title = extract_field(soup, rules.title) or candidate.title
        published_raw, modified_raw = (
            extract_field(soup, rules.published),
            extract_field(soup, rules.modified),
        )
        author = extract_field(soup, rules.author)
        for selector in rules.remove_selectors:
            for node in soup.select(selector):
                node.decompose()
        body = extract_field(soup, rules.body)
        if not external_id or not title or len(body) < rules.minimum_body_chars:
            raise SourceError("详情身份、标题或正文未达到配置要求，不能声明采集成功")
        status = "preview" if paywall else "truncated" if len(body) > rules.maximum_body_chars else "fulltext"
        content = CollectedContent(
            body[: rules.maximum_body_chars], status, PARSER_VERSION, body_links(soup, rules.body, canonical)
        )
        return CollectedRecord(
            source=self.plan.source_id,
            external_id=external_id,
            title=title,
            abstract=body[:1500],
            url=canonical,
            published=timestamp(published_raw) or candidate.published,
            updated=timestamp(modified_raw),
            metadata={
                "material_type": self.plan.material_type,
                "author": author,
                "published_raw": published_raw,
                "modified_raw": modified_raw,
                "modified_precision": date_precision(modified_raw),
                "date_precision": "instant" if candidate.published else date_precision(published_raw),
                "plan_hash": self.plan.fingerprint(),
                "access_state": status,
                "body_hash": hashlib.sha256(content.text.encode()).hexdigest(),
            },
            content=content,
        )

    def _coverage(self) -> str:
        return "configured_current_window; discovery_and_updates; bounded_pages; no_historical_coverage_claim"
