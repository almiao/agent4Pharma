"""PubMed 连接器：保存搜索 ID 快照，分页取得摘要；截断或缺失记录显式失败。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from agent4pharma.infrastructure.connectors.source_abstracts import source_abstract_metadata
from agent4pharma.infrastructure.connectors.transport import Transport
from agent4pharma.infrastructure.connectors.xml import text, xml
from agent4pharma.modules.collection.errors import SourceError
from agent4pharma.modules.materials.collection_records import CollectedRecord, CollectionPage, SourceResponse


class PubMed:
    """PubMed ID 快照分页适配器；不把搜索结果截断视作完成。"""

    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"

    def __init__(self, transport: Transport):
        self.http = transport

    def page(self, start: datetime, end: datetime, query: str, cursor: dict | None) -> CollectionPage:
        """获取 [start, end) 对应来源窗口的一页；游标为空开始扫描，来源失败显式抛错。"""
        raw = []
        if cursor is None:
            response, cursor = self._search(start, end, query)
            raw.append(response)
        ids, offset, total = cursor["ids"], cursor["offset"], cursor["total"]
        page_ids = ids[offset : offset + 100]
        records = []
        if page_ids:
            params = {"db": "pubmed", "id": ",".join(page_ids), "retmode": "xml"}
            if os.getenv("NCBI_API_KEY"):
                params["api_key"] = os.environ["NCBI_API_KEY"]
            response = self.http.get("pubmed", self.base + "efetch.fcgi", params)
            raw.append(response)
            records = self._parse_articles(response)
            if {r.external_id for r in records} != set(page_ids):
                raise SourceError("PubMed EFetch missing IDs or unsupported record types; page not committed")
        offset += len(page_ids)
        next_cursor = {**cursor, "offset": offset} if offset < total else None
        return CollectionPage(
            records,
            next_cursor,
            raw,
            total,
            "PubMed EDAT inclusive date envelope; not publication date; modifications not monitored",
        )

    def _search(self, start: datetime, end: datetime, query: str) -> tuple[SourceResponse, dict]:
        # Source endpoints have inclusive dates; our workflow uses an exclusive end.
        last_day = end - timedelta(microseconds=1)
        dates = f'("{start:%Y/%m/%d}"[EDAT] : "{last_day:%Y/%m/%d}"[EDAT])'
        params = {
            "db": "pubmed",
            "term": f"({query}) AND {dates}" if query else dates,
            "retmode": "json",
            "retmax": 9999,
            "sort": "pub_date",
            "tool": "agent4Pharma",
        }
        if os.getenv("NCBI_API_KEY"):
            params["api_key"] = os.environ["NCBI_API_KEY"]
        if os.getenv("NCBI_EMAIL"):
            params["email"] = os.environ["NCBI_EMAIL"]
        response = self.http.get("pubmed", self.base + "esearch.fcgi", params)
        result = json.loads(response.body).get("esearchresult", {})
        if "errorlist" in result or "ERROR" in result:
            raise SourceError("PubMed search error; inspect query")
        if "count" not in result:
            raise SourceError("PubMed missing count")
        total = int(result["count"])
        if total >= 10000:
            raise SourceError("PubMed >=10000 results: narrow/split query; not marked complete")
        cursor = {"ids": result.get("idlist", []), "offset": 0, "total": total}
        if len(cursor["ids"]) != total:
            raise SourceError("PubMed ID list truncated")
        return response, cursor

    def _parse_articles(self, response: SourceResponse) -> list[CollectedRecord]:
        records = []
        root = xml(response)
        for item in root.findall("PubmedArticle"):
            pmid = item.findtext("MedlineCitation/PMID", "")
            if not pmid:
                raise SourceError("PubMed article has no PMID")
            article = item.find("MedlineCitation/Article")
            if article is None:
                raise SourceError("PubMed missing article")
            identifiers = {"pmid": pmid}
            for entry in item.findall("PubmedData/ArticleIdList/ArticleId"):
                if entry.get("IdType") == "doi" and entry.text:
                    identifiers["doi"] = entry.text
            abstract = "\n".join(
                f"{e.get('Label', '')}: {text(e)}".lstrip(": ")
                for e in article.findall("Abstract/AbstractText")
            )
            date = article.find("Journal/JournalIssue/PubDate")
            published = (
                "-".join(text(date.find(k)) for k in ("Year", "Month", "Day")) if date is not None else None
            )
            records.append(
                CollectedRecord(
                    "pubmed",
                    pmid,
                    text(article.find("ArticleTitle")),
                    abstract,
                    [text(a) for a in article.findall("AuthorList/Author")],
                    identifiers,
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    published=published,
                    metadata={
                        "date_precision": "source_text",
                        "record_kind": "journal_record",
                        "journal": text(article.find("Journal/Title")),
                        "affiliations": list(
                            dict.fromkeys(
                                text(a)
                                for a in article.findall("AuthorList/Author/AffiliationInfo/Affiliation")
                                if text(a)
                            )
                        ),
                        **source_abstract_metadata(abstract, "pubmed_api", "Abstract/AbstractText"),
                    },
                )
            )
        return records
