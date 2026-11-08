"""来源 HTTP 传输：跨进程限流、有限重试、Retry-After 延期、URL 脱敏与响应留档。"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from agent4pharma.modules.collection.acquisition_interfaces import SourceAccessPersistence
from agent4pharma.modules.collection.errors import SourceError
from agent4pharma.modules.materials.collection_records import SourceResponse


def safe_url(url: str) -> str:
    """移除已知密钥查询参数及片段后返回可留档 URL，不改变非敏感查询范围。"""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in {"api_key", "key", "token"}]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


class Transport:
    """保留成功响应并约束上游请求；长延期落盘后返回失败，交由任务后续续跑。"""

    def __init__(self, store: SourceAccessPersistence, client: httpx.Client | None = None, sleep=time.sleep):
        self.store = store
        self.client = client or httpx.Client(
            timeout=40, follow_redirects=True, headers={"User-Agent": "agent4Pharma/0.1 metadata-discovery"}
        )
        self.sleep = sleep

    def close(self):
        """释放本对象拥有的资源；业务状态和已提交数据不随关闭改变。"""
        self.client.close()

    def get(self, source: str, url: str, params: dict | None = None) -> SourceResponse:
        """按共享额度发送请求并留档成功响应；有限重试耗尽或长延期时抛出安全 SourceError。"""
        group = "biorxiv-api" if source in {"biorxiv", "medrxiv"} else source
        interval = (
            3.1
            if source == "arxiv"
            else 1.0
            if group == "biorxiv-api"
            else (0.11 if os.getenv("NCBI_API_KEY") else 0.35)
        )
        for attempt in range(4):
            wait = self.store.reserve_request(group, interval)
            if wait > 60:
                raise SourceError(f"{source}: access budget deferred for {wait:.0f}s")
            self.sleep(wait)
            try:
                response = self.client.get(url, params=params)
            except httpx.TransportError as exc:
                if attempt == 3:
                    # httpx exception strings can contain API keys from query URLs.
                    raise SourceError(f"{source}: network {type(exc).__name__}") from None
                self.sleep(2**attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 3:
                    raise SourceError(f"{source}: HTTP {response.status_code}, retries exhausted")
                delay = retry_delay(response.headers.get("Retry-After", ""), attempt)
                if delay > 60:
                    # Defer to the next persisted workflow retry instead of long blocking sleeps.
                    self.store.defer_access(group, delay)
                    raise SourceError(f"{source}: retry deferred, Retry-After={delay:.0f}s")
                self.sleep(delay)
                continue
            if response.status_code != 200:
                raise SourceError(f"{source}: HTTP {response.status_code}")
            raw = SourceResponse(
                response.content, safe_url(str(response.url)), response.headers.get("Content-Type", "")
            )
            # Preserve successful source responses even if parsing later fails.
            self.store.archive_response(
                raw.body,
                {
                    "url": raw.url,
                    "content_type": raw.content_type,
                    "stage": "received",
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            return raw
        raise AssertionError("unreachable")


def retry_delay(retry: str, attempt: int) -> float:
    """解释 Retry-After 秒数或 HTTP 日期，与本次退避取较大值；无效值使用退避。"""
    delay = 2**attempt
    if retry:
        try:
            delay = max(delay, float(retry))
        except ValueError:
            try:
                delay = max(
                    delay,
                    (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds(),
                )
            except (ValueError, TypeError):
                pass
    return delay
