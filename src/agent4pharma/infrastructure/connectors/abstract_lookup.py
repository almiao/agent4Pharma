"""摘要获取：Europe PMC 批量查询优先，OpenAlex 补缺，缺失显式记为 none。

该适配器只解决“已有 DOI 的论文取摘要”，不负责发现或全文。
批量查询把每页几十条论文压缩成一两次请求，避免逐篇访问出版商。
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

import httpx

EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
OPENALEX = "https://api.openalex.org/works"
USER_AGENT = "agent4Pharma/0.1 abstract-enrichment"
CHUNK_SIZE = 20
DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/)?", re.I)


@dataclass(frozen=True)
class AbstractHit:
    """单篇摘要结果；status=none 表示可确认上游没有摘要，不是暂时失败。"""

    doi: str
    status: Literal["found", "none", "error"]
    source: str = ""
    text: str = ""
    detail: str = ""


def normalize_doi(value: str) -> str:
    """统一 DOI 形式，便于跨来源比对；空值返回空串。"""
    return DOI_PREFIX.sub("", (value or "").strip()).lower()


def is_placeholder(text: str) -> bool:
    """识别 RSS 把“期刊, Published online: 日期; doi:…”写进摘要字段的情况。"""
    value = (text or "").strip()
    if not value:
        return True
    return "Published online" in value and "doi:" in value and len(value) < 400


class AbstractLookup:
    """按 DOI 批量取摘要；延迟与重试有界，失败不伪装成成功。"""

    def __init__(
        self,
        client: httpx.Client | None = None,
        interval: float = 0.25,
        sleep=time.sleep,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )
        self.interval = interval
        self.sleep = sleep
        self._last = 0.0

    def close(self) -> None:
        """关闭自建 HTTP 连接；调用方注入的 client 由调用方负责。"""
        if self._owns_client:
            self.client.close()

    def _wait(self) -> None:
        remaining = self.interval - (time.monotonic() - self._last)
        if remaining > 0:
            self.sleep(remaining)
        self._last = time.monotonic()

    def _get(self, url: str, params: dict) -> dict | None:
        for attempt in range(2):
            self._wait()
            try:
                response = self.client.get(url, params=params)
            except httpx.HTTPError:
                if attempt == 1:
                    return None
                self.sleep(1.5)
                continue
            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError:
                    return None
                return payload if isinstance(payload, dict) else None
            if response.status_code in {429, 500, 502, 503, 504} and attempt == 0:
                self.sleep(2.0)
                continue
            return None
        return None

    def fetch(self, dois: Iterable[str]) -> dict[str, AbstractHit]:
        """返回 DOI → 结果；未查到的 DOI 明确记为 none，查询失败记为 error。"""
        wanted = sorted({normalize_doi(doi) for doi in dois if normalize_doi(doi)})
        if not wanted:
            return {}
        found: dict[str, AbstractHit] = {}
        errors: set[str] = set()
        for start in range(0, len(wanted), CHUNK_SIZE):
            chunk = wanted[start : start + CHUNK_SIZE]
            payload = self._epmc(chunk)
            if payload is None:
                errors.update(chunk)
            else:
                found.update(payload)
        missing = [doi for doi in wanted if doi not in found]
        if missing:
            for start in range(0, len(missing), CHUNK_SIZE):
                chunk = missing[start : start + CHUNK_SIZE]
                payload = self._openalex(chunk)
                if payload is None:
                    errors.update(chunk)
                else:
                    found.update(payload)
        result: dict[str, AbstractHit] = {}
        for doi in wanted:
            if doi in found:
                result[doi] = found[doi]
            elif doi in errors:
                result[doi] = AbstractHit(doi=doi, status="error", detail="upstream_unavailable")
            else:
                result[doi] = AbstractHit(doi=doi, status="none")
        return result

    def _epmc(self, dois: list[str]) -> dict[str, AbstractHit] | None:
        query = " OR ".join(f'DOI:"{doi}"' for doi in dois)
        payload = self._get(
            EUROPEPMC, {"query": query, "format": "json", "resultType": "core", "pageSize": 100}
        )
        if payload is None:
            return None
        hits: dict[str, AbstractHit] = {}
        for item in payload.get("resultList", {}).get("result", []):
            doi = normalize_doi(item.get("doi") or "")
            text = (item.get("abstractText") or "").strip()
            if doi and text and doi not in hits:
                hits[doi] = AbstractHit(doi=doi, status="found", source="europepmc", text=text)
        return hits

    def _openalex(self, dois: list[str]) -> dict[str, AbstractHit] | None:
        payload = self._get(
            OPENALEX,
            {
                "filter": "doi:" + "|".join(dois),
                "select": "doi,abstract_inverted_index",
                "per-page": CHUNK_SIZE,
            },
        )
        if payload is None:
            return None
        hits: dict[str, AbstractHit] = {}
        for item in payload.get("results", []):
            doi = normalize_doi(item.get("doi") or "")
            text = reconstruct_abstract(item.get("abstract_inverted_index"))
            if doi and text:
                hits[doi] = AbstractHit(doi=doi, status="found", source="openalex", text=text)
        return hits


def reconstruct_abstract(index: dict | None) -> str:
    """把 OpenAlex 的倒排索引还原为正文；缺失或格式异常返回空串。"""
    if not isinstance(index, dict) or not index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, slots in index.items():
        if not isinstance(slots, list):
            return ""
        positions.extend((slot, word) for slot in slots if isinstance(slot, int))
    return " ".join(word for _, word in sorted(positions))
