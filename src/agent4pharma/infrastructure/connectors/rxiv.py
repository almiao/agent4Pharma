"""bioRxiv/medRxiv 共用 API 适配：按来源日期分页获取资料并保留版本和出处。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from agent4pharma.infrastructure.connectors.source_abstracts import source_abstract_metadata
from agent4pharma.infrastructure.connectors.transport import Transport
from agent4pharma.modules.collection.errors import PaginationChanged, SourceError
from agent4pharma.modules.materials.collection_records import CollectedRecord, CollectionPage


class Rxiv:
    """共享 bioRxiv/medRxiv API 的来源适配器；来源键决定身份和查询端点。"""

    def __init__(self, source: str, transport: Transport):
        self.source, self.http = source, transport

    def page(self, start: datetime, end: datetime, query: str, cursor: dict | None) -> CollectionPage:
        """获取 [start, end) 对应来源窗口的一页；游标为空开始扫描，来源失败显式抛错。"""
        offset = (cursor or {}).get("offset", 0)
        last_day = end - timedelta(microseconds=1)
        url = (
            f"https://api.biorxiv.org/details/{self.source}/"
            f"{start:%Y-%m-%d}/{last_day:%Y-%m-%d}/{offset}/json"
        )
        response = self.http.get(self.source, url)
        payload = json.loads(response.body)
        messages = payload.get("messages", [])
        if not messages or messages[0].get("status") != "ok":
            raise SourceError(f"{self.source}: API status not ok")
        if "total" not in messages[0] or "collection" not in payload:
            raise SourceError(f"{self.source}: incomplete API response")
        total = int(messages[0]["total"])
        records = []
        for item in payload["collection"]:
            doi = item.get("doi")
            if not doi:
                raise SourceError(f"{self.source}: missing DOI")
            records.append(
                CollectedRecord(
                    self.source,
                    doi,
                    item["title"],
                    item.get("abstract", ""),
                    item.get("authors", "").split(";"),
                    {"doi": doi},
                    str(item["version"]),
                    f"https://doi.org/{doi}",
                    item.get("date"),
                    item.get("date"),
                    {
                        "category": item.get("category"),
                        "published_doi": item.get("published"),
                        "date_precision": "day",
                        **source_abstract_metadata(
                            item.get("abstract", ""), self.source + "_api", "abstract"
                        ),
                    },
                )
            )
        if not records and offset < total:
            raise SourceError(f"{self.source}: empty page before reported end")
        if cursor and cursor.get("total") != total:
            raise PaginationChanged(f"{self.source}: result count changed during paging; restarting window")
        next_offset = offset + len(records)
        return CollectionPage(
            records,
            {"offset": next_offset, "total": total} if next_offset < total else None,
            [response],
            total,
            "Source date-only inclusive envelope; source timezone unspecified",
        )
