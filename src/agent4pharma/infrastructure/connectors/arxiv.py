"""arXiv 连接器：按 UTC 提交时间查询、解析预印本身份，并检测分页总数变化。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from agent4pharma.infrastructure.connectors.source_abstracts import source_abstract_metadata
from agent4pharma.infrastructure.connectors.transport import Transport
from agent4pharma.infrastructure.connectors.xml import text, xml
from agent4pharma.modules.collection.errors import PaginationChanged, SourceError
from agent4pharma.modules.materials.collection_records import CollectedRecord, CollectionPage


class Arxiv:
    """arXiv 提交时间查询适配器；连接器不拥有注入的传输实例。"""

    ns = {
        "a": "http://www.w3.org/2005/Atom",
        "o": "http://a9.com/-/spec/opensearch/1.1/",
        "x": "http://arxiv.org/schemas/atom",
    }

    def __init__(self, transport: Transport):
        self.http = transport

    def page(self, start: datetime, end: datetime, query: str, cursor: dict | None) -> CollectionPage:
        """获取 [start, end) 对应来源窗口的一页；游标为空开始扫描，来源失败显式抛错。"""
        offset = (cursor or {}).get("offset", 0)
        # submittedDate is in GMT; convert the broad query envelope explicitly.
        left = start.astimezone(timezone.utc)
        right = (end - timedelta(minutes=1)).astimezone(timezone.utc)
        date_query = f"submittedDate:[{left:%Y%m%d%H%M} TO {right:%Y%m%d%H%M}]"
        response = self.http.get(
            "arxiv",
            "https://export.arxiv.org/api/query",
            {
                "search_query": f"({query}) AND {date_query}" if query else date_query,
                "start": offset,
                "max_results": 100,
                "sortBy": "submittedDate",
                "sortOrder": "ascending",
            },
        )
        root = xml(response)
        count = root.findtext("o:totalResults", namespaces=self.ns)
        if count is None:
            raise SourceError("arXiv missing totalResults")
        total = int(count)
        if total > 30000:
            raise SourceError("arXiv >30000 results: split query")
        records = [self._parse_entry(entry) for entry in root.findall("a:entry", self.ns)]
        if not records and offset < total:
            raise SourceError("arXiv empty page before reported end")
        next_offset = offset + len(records)
        if cursor and cursor.get("total") != total:
            raise PaginationChanged("arXiv result count changed during paging; restarting window")
        return CollectionPage(
            records,
            {"offset": next_offset, "total": total} if next_offset < total else None,
            [response],
            total,
            "arXiv submittedDate UTC envelope; revisions/late indexing not guaranteed",
        )

    def _parse_entry(self, entry: ET.Element) -> CollectedRecord:
        """Journal DOI is a related publication, not this preprint identity."""
        url = entry.findtext("a:id", "", self.ns)
        if "/api/errors" in url:
            raise SourceError("arXiv query error")
        raw_id = url.split("/abs/")[-1]
        match = re.fullmatch("(.+?)v(\\d+)", raw_id)
        external_id, version = match.groups() if match else (raw_id, "1")
        if not external_id:
            raise SourceError("arXiv missing ID")
        abstract = text(entry.find("a:summary", self.ns))
        return CollectedRecord(
            "arxiv",
            external_id,
            text(entry.find("a:title", self.ns)),
            abstract,
            [text(a.find("a:name", self.ns)) for a in entry.findall("a:author", self.ns)],
            {"arxiv": external_id},
            version,
            url,
            entry.findtext("a:published", namespaces=self.ns),
            entry.findtext("a:updated", namespaces=self.ns),
            {
                "related_journal_doi": entry.findtext("x:doi", namespaces=self.ns),
                **source_abstract_metadata(abstract, "arxiv_api", "summary"),
            },
        )
