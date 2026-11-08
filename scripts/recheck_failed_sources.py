"""逐源联网复核失败入口、RSS 内容协商和官方替代链接；仅输出证据，不调用模型。"""

import argparse
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from agent4pharma.composition import discovery_run_view
from agent4pharma.infrastructure.connectors.rss_feed import RssPageFetcher, parse_feed
from agent4pharma.infrastructure.persistence.sqlite.management_sources import ManagedSourceRepository
from agent4pharma.infrastructure.web.public_fetch import PublicPageFetcher
from agent4pharma.modules.sources.management import (
    ManagedSource,
    SourceAccessCheck,
    SourceCollectionAvailability,
)
from agent4pharma.shared.url_validation import public_reference_url


def feed_identity(body: bytes, name: str) -> bool:
    """要求频道或出版物明确自报本刊名称，避免误将姐妹刊当成功。"""

    def normalize(value: str) -> str:
        value = re.sub(r"^(ACS Publications|ScienceDirect Publication):\s*", "", value, flags=re.I)
        return re.sub(r"[^a-z0-9]+", " ", value.lower().replace("&", " and ")).strip()

    wanted = normalize(name)
    root = ElementTree.fromstring(body)
    labels = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in {"channel", "feed"}:
            labels.extend(
                "".join(child.itertext()).strip() for child in node if child.tag.rsplit("}", 1)[-1] == "title"
            )
        if tag == "publicationName" and node.text:
            labels.append(node.text)
    return any(
        normalize(label) == wanted
        or any(
            normalize(label).startswith(wanted + " " + suffix)
            for suffix in ("asap", "current", "latest", "advance access", "rss", "acs publications")
        )
        for label in labels
    )


def probe(
    url: str, name: str, mode: str, fetcher: PublicPageFetcher, output: Path
) -> tuple[SourceAccessCheck, list[str]]:
    """读取和正式解析发生在事务外；保存响应哈希，响应正文不进入可见错误。"""
    check = SourceAccessCheck(
        url=url, checked_at=datetime.now(timezone.utc), mode=mode, outcome="request_error"
    )
    links = []
    try:
        doc = fetcher.fetch(url)
        check.final_url, check.http_status = doc.url, doc.http_status
        check.sha256 = hashlib.sha256(doc.body).hexdigest()
        (output / check.sha256).write_bytes(doc.body)
        if doc.http_status != 200:
            check.outcome = "http_error"
            check.reason = f"HTTP {doc.http_status}"
            if any(
                marker in doc.body.lower() for marker in (b"challenge-platform", b"cf-chl-", b"just a moment")
            ):
                check.reason += "；响应包含站点访问验证页面标记"
            return check, links
        if mode == "homepage":
            soup = BeautifulSoup(doc.body, "html.parser")
            for link in soup.select("link[href], a[href]"):
                target = urljoin(doc.url, str(link.get("href", "")))
                hint = (str(link.get("type", "")) + " " + target).lower()
                if not any(key in hint for key in ("rss", "atom+xml", "/feed/")):
                    continue
                try:
                    public_reference_url(target)
                except ValueError:
                    continue
                if urlsplit(target).hostname == urlsplit(doc.url).hostname:
                    links.append(target)
            check.outcome, check.reason = "not_feed", "主页可达，已检查页面中的 RSS/Atom 链接"
            return check, list(dict.fromkeys(links))[:3]
        try:
            rows = parse_feed(doc.body, doc.url, "recheck")
            if not feed_identity(doc.body, name):
                check.outcome, check.reason = "identity_mismatch", "响应可解析，但未确认频道属于此刊物"
            else:
                check.entries = len(rows)
                check.outcome = "feed" if rows else "empty_feed"
                check.reason = f"正式 RSS 解析器通过，频道身份一致，{len(rows)} 条题录"
        except (ValueError, ElementTree.ParseError) as exc:
            check.outcome, check.reason = "parse_error", "响应未通过正式 RSS 解析：" + type(exc).__name__
    except ValueError as exc:
        check.reason = "地址/跳转校验失败：" + str(exc)
    except OSError as exc:
        check.reason = "网络请求失败：" + type(exc).__name__
    return check, links


def audit_source(
    source: ManagedSource, entry: str, alternatives: list[str], output: Path
) -> SourceCollectionAvailability:
    """每来源最多十一次有界检查；没有可用 Feed 时复核主页及其明确链接。"""
    normal, feed = (
        PublicPageFetcher(timeout=10, max_bytes=4_000_000),
        RssPageFetcher(timeout=10, max_bytes=4_000_000),
    )
    checks = []
    for url, mode, fetcher in [(entry, "configured_entry", normal), (entry, "feed_accept", feed)]:
        check, _ = probe(url, source.name, mode, fetcher, output)
        checks.append(check)
        if check.outcome in {"feed", "empty_feed"}:
            break
    good = next((c for c in checks if c.outcome in {"feed", "empty_feed"}), None)
    if not good:
        home, found = probe(source.homepage_url or entry, source.name, "homepage", normal, output)
        checks.append(home)
        for url in list(dict.fromkeys(alternatives + found))[:4]:
            if url == entry:
                continue
            for fetcher in (normal, feed):
                check, _ = probe(url, source.name, "discovered_feed", fetcher, output)
                checks.append(check)
                if check.outcome in {"feed", "empty_feed"}:
                    good = check
                    break
            if good:
                break
    if good:
        reason = f"可用入口已验证：{good.entries} 条题录。"
        if good.url != entry:
            reason = "原入口不可用；已验证官方替代 RSS，" + str(good.entries) + " 条题录。"
        status = "available"
    else:
        outcomes = "；".join(dict.fromkeys(c.reason for c in checks if c.mode != "homepage"))
        status = "needs_configuration" if source.access_method == "html" else "currently_unavailable"
        reason = (
            "仅登记 HTML 主页，尚未取得可验证的 RSS/API；"
            if status == "needs_configuration"
            else "当前环境未取得可用 Feed；"
        ) + outcomes
    return SourceCollectionAvailability(
        status=status, reason=reason[:2000], checked_at=datetime.now(timezone.utc), checks=checks
    )


def main() -> None:
    """显式联网复核当前失败来源并输出证据，不改历史任务或自动启用来源。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--batch", required=True)
    parser.add_argument("--alternatives", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    sources = {s.id: s for s in ManagedSourceRepository(root).list_sources(limit=200)}
    failed = [s for s in discovery_run_view(root, args.batch).sources if s.status in {"failed", "partial"}]
    alternatives = json.loads(args.alternatives.read_text(encoding="utf-8")) if args.alternatives else {}
    output = args.output or root / "reports" / (
        "source-failure-recheck-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    )
    output.mkdir(parents=True, exist_ok=True)
    result = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = {
            pool.submit(
                audit_source, sources[s.source_id], s.request_url, alternatives.get(s.source_id, []), output
            ): s.source_id
            for s in failed
        }
        for future in as_completed(pending):
            source_id = pending[future]
            assessment = future.result()
            result[source_id] = assessment.model_dump(mode="json")
            print(
                json.dumps(
                    {
                        "source": sources[source_id].name,
                        "status": assessment.status,
                        "reason": assessment.reason,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            (output / "assessment.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )


if __name__ == "__main__":
    main()
