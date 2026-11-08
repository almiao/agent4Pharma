"""逐刊调查官方 RSS 候选并通过正式读取/解析器验证，不把 URL 推测当作接入成功。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import parse_qs, quote, urljoin, urlsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from agent4pharma.infrastructure.connectors.rss_feed import parse_feed
from agent4pharma.infrastructure.web.public_fetch import PublicPageFetcher
from agent4pharma.shared.clock import utcnow
from agent4pharma.shared.url_validation import public_reference_url


class JournalCandidate(BaseModel):
    """人工整理的候选，只有实际官方响应及刊名核对后才可用。"""

    name: str
    homepage: str
    feeds: list[str]


class FeedAttempt(BaseModel):
    """不包含认证信息的单次访问结果。"""

    url: str
    final_url: str = ""
    status: str
    http_status: int | None = None
    content_type: str = ""
    error: str = ""
    entries: int = 0
    identity_labels: list[str] = Field(default_factory=list)
    identity_basis: str = "channel/publication title"


class JournalAudit(BaseModel):
    """完整逐刊结果；不可用来源保留明确原因。"""

    name: str
    homepage: str
    checked_at: str
    status: str
    usable: bool = False
    feed_url: str = ""
    entries: int = 0
    summaries: int = 0
    precise_dates: int = 0
    sha256: str = ""
    attempts: list[FeedAttempt] = Field(default_factory=list)


def normalized(value: str) -> str:
    """刊名仅移除标点与冠词，不对任意缩写作模糊匹配。"""
    value = re.sub(r"^ScienceDirect Publication:\s*", "", value, flags=re.IGNORECASE)
    words = re.sub(r"[^a-z0-9]+", " ", value.lower().replace("&", " and ")).split()
    return " ".join(words[1:] if words[:1] == ["the"] else words)


def identity_labels(body: bytes) -> list[str]:
    """读取 Feed 自报的频道名与出版物名，用于避免误收其他刊物。"""
    root = ElementTree.fromstring(body)
    labels = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "publicationName" and node.text:
            labels.append(node.text.strip())
        if tag in {"channel", "feed"}:
            labels += [
                "".join(child.itertext()).strip() for child in node if child.tag.rsplit("}", 1)[-1] == "title"
            ]
    return list(dict.fromkeys(labels))


def matches_journal(name: str, labels: list[str]) -> bool:
    """只接受明确刊名或常见频道后缀，不以短刊名子串误认姐妹刊。"""
    wanted = normalized(name)
    return any(
        normalized(label) == wanted or normalized(label).startswith(wanted + " " + suffix)
        for label in labels
        for suffix in ("latest", "current", "advance", "issue", "recent", "rss", "articles", "ahead")
    )


def verify_springer_filter(url: str, body: bytes, fetcher: PublicPageFetcher) -> list[str]:
    """通用 Latest Results 必须核对每条 DOI 的刊号及首条 Crossref 刊名，不能凭频道名通过。"""
    parts = urlsplit(url)
    journal_id = parse_qs(parts.query).get("facet-journal-id", [""])[0]
    if parts.hostname != "link.springer.com" or not journal_id.isdigit():
        return []
    root = ElementTree.fromstring(body)
    links = [
        child.text or ""
        for item in root.iter()
        if item.tag.rsplit("}", 1)[-1] == "item"
        for child in item
        if child.tag.rsplit("}", 1)[-1] == "link"
    ]
    if not links or any(not re.search(r"/10\.[0-9]+/s" + journal_id + r"-", link) for link in links):
        return []
    for link in links[:3]:
        doi = link.split("/article/", 1)[-1]
        response = fetcher.fetch("https://api.crossref.org/works/" + quote(doi, safe=""))
        if response.http_status == 200:
            data = json.loads(response.body)
            return data.get("message", {}).get("container-title", [])
    return []


def discover_links(homepage: str, fetcher: PublicPageFetcher) -> tuple[list[str], FeedAttempt]:
    """从真实主页发现 RSS 链接；失败单独留证，不标成该刊绝无 RSS。"""
    try:
        page = fetcher.fetch(homepage)
        status = "homepage_read" if page.http_status == 200 else "homepage_http_error"
        attempt = FeedAttempt(
            url=homepage,
            final_url=page.url,
            status=status,
            http_status=page.http_status,
            content_type=page.content_type,
        )
        if page.http_status != 200:
            return [], attempt
        soup = BeautifulSoup(page.body, "html.parser")
        links = []
        for node in soup.select("link[href], a[href]"):
            href = str(node.get("href", ""))
            hint = (str(node.get("type", "")) + " " + href).lower()
            if not any(key in hint for key in ("rss", "atom+xml", "/feed/", "feed=rss")):
                continue
            target = urljoin(page.url, href)
            try:
                public_reference_url(target)
            except ValueError:
                continue
            if urlsplit(target).hostname and not target.endswith((".js", ".css")):
                links.append(target)
        return list(dict.fromkeys(links))[:6], attempt
    except (ValueError, OSError) as exc:
        return [], FeedAttempt(url=homepage, status="homepage_access_failed", error=type(exc).__name__)


def audit_one(candidate: JournalCandidate, fetcher: PublicPageFetcher, output: Path) -> JournalAudit:
    """最多验证候选及主页发现的六条新链接，保存真实 Feed 和解析后条目。"""
    from dataclasses import asdict

    result = JournalAudit(
        name=candidate.name, homepage=candidate.homepage, checked_at=utcnow(), status="unavailable"
    )
    queue, seen, discovered = list(candidate.feeds), set(), False
    while queue or not discovered:
        if not queue:
            found, attempt = discover_links(candidate.homepage, fetcher)
            result.attempts.append(attempt)
            queue, discovered = [url for url in found if url not in seen], True
            if not queue:
                break
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        attempt = FeedAttempt(url=url, status="access_failed")
        result.attempts.append(attempt)
        try:
            document = fetcher.fetch(url)
            attempt.final_url, attempt.http_status = document.url, document.http_status
            attempt.content_type = document.content_type
            if document.http_status != 200:
                attempt.status = "http_error"
                continue
            rows = parse_feed(document.body, document.url, candidate.name)
            attempt.entries = len(rows)
            attempt.identity_labels = identity_labels(document.body)
            if not matches_journal(candidate.name, attempt.identity_labels):
                crossref_labels = verify_springer_filter(document.url, document.body, fetcher)
                if crossref_labels:
                    attempt.identity_labels += crossref_labels
                    attempt.identity_basis = (
                        "all item DOI journal codes + sampled DOI Crossref container-title"
                    )
            if not rows:
                attempt.status = "empty_feed"
                continue
            if not matches_journal(candidate.name, attempt.identity_labels):
                attempt.status = "identity_unverified"
                continue
            attempt.status = "usable"
            result.status, result.usable, result.feed_url = "usable", True, url
            result.entries = len(rows)
            result.summaries = sum(bool(row.abstract) for row in rows)
            result.precise_dates = sum(bool(row.published) for row in rows)
            result.sha256 = hashlib.sha256(document.body).hexdigest()
            key = hashlib.sha256(candidate.name.encode()).hexdigest()[:16]
            output.joinpath(key + ".xml").write_bytes(document.body)
            output.joinpath(key + ".json").write_text(
                json.dumps([asdict(row) for row in rows], ensure_ascii=False, indent=2), encoding="utf-8"
            )
            break
        except (ValueError, OSError, ElementTree.ParseError) as exc:
            attempt.status = "parse_failed" if attempt.http_status == 200 else "access_failed"
            attempt.error = type(exc).__name__
    if not result.usable:
        result.status = "unavailable_no_feed_found" if not seen else "unavailable_access_or_parse"
    return result


def main() -> None:
    """批量审计且逐条写入检查点；不启动模型或直接修改业务数据库。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=Path("config/journal-rss-candidates.tsv"))
    parser.add_argument("--output", type=Path, default=Path("data/reports/journal-rss"))
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    candidates = [
        JournalCandidate(
            name=row["name"], homepage=row["homepage"], feeds=row["feeds"].split("|") if row["feeds"] else []
        )
        for row in csv.DictReader(args.candidates.open(encoding="utf-8"), delimiter="\t")
    ]
    catalog = {
        row["name"]
        for row in csv.DictReader(Path("data/reports/venue-catalog.csv").open(encoding="utf-8-sig"))
        if row["kind"] == "journal" and row["watch_status"] in {"watch", "candidate"}
    }
    if len(candidates) != len(catalog) or {row.name for row in candidates} != catalog:
        raise ValueError("候选表必须逐一覆盖当前关注/候选期刊，不能漏刊或重复")
    args.output.mkdir(parents=True, exist_ok=True)
    checkpoint = args.output / "audit.json"
    old = (
        json.loads(checkpoint.read_text(encoding="utf-8"))
        if checkpoint.exists() and args.retry_failed
        else []
    )
    results = {row["name"]: JournalAudit.model_validate(row) for row in old if row["usable"]}
    fetcher = PublicPageFetcher(max_bytes=4_000_000, timeout=18)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(audit_one, row, fetcher, args.output): row.name
            for row in candidates
            if row.name not in results
        }
        for future in as_completed(futures):
            row = future.result()
            results[row.name] = row
            checkpoint.write_text(
                json.dumps(
                    [results[key].model_dump() for key in sorted(results)], ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
            print(
                f"{len(results)}/{len(candidates)} {row.name}: {row.status}, entries={row.entries}",
                flush=True,
            )


if __name__ == "__main__":
    main()
