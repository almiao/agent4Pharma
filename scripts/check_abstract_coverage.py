"""逐刊检查摘要可获取性：每本刊取一篇有 DOI 的论文，记录来源或明确记为 none。"""

import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent4pharma.infrastructure.connectors.abstract_lookup import AbstractLookup  # noqa: E402

EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
HEADERS = {"User-Agent": "agent4pharma-abstract-check/0.1 (mailto:research@example.org)"}


def venues() -> list[tuple[str, str]]:
    con = sqlite3.connect(ROOT / "data/discovery.sqlite3")
    try:
        return con.execute(
            "SELECT name, watch_status FROM venues WHERE watch_status IN ('watch','candidate') "
            "ORDER BY watch_status, name"
        ).fetchall()
    finally:
        con.close()


def crossref_sample(client: httpx.Client, journal: str) -> tuple[str, str]:
    """Europe PMC 名称不匹配时用 Crossref 容器名取一篇 DOI；仅用于定位样本。"""
    try:
        response = client.get(
            "https://api.crossref.org/works",
            params={
                "query.container-title": journal,
                "rows": 5,
                "filter": "type:journal-article,from-pub-date:2024-01-01",
                "select": "DOI,title,container-title",
            },
            headers=HEADERS,
        )
        if response.status_code != 200:
            return "", ""
        items = response.json().get("message", {}).get("items", [])
    except httpx.HTTPError:
        return "", ""
    finally:
        time.sleep(0.3)
    for item in items:
        container = (item.get("container-title") or [""])[0].casefold().replace(".", "")
        if container and container in journal.casefold().replace(".", ""):
            return item.get("DOI", ""), (item.get("title") or [""])[0]
    return "", ""


def name_variants(journal: str) -> list[str]:
    """PubMed 规范刊名与出版社显示名常有差异，按已知规则补候选。"""
    variants = [journal]
    stripped = journal.removeprefix("The ")
    if stripped != journal:
        variants.append(stripped)
    elif not journal.startswith("The "):
        variants.append("The " + journal)
    if journal.startswith("The Lancet "):
        variants.append("The Lancet. " + journal[len("The Lancet ") :])
        variants.append("Lancet. " + journal[len("The Lancet ") :])
    if journal.startswith("Nature Reviews "):
        variants.append("Nature reviews. " + journal[len("Nature Reviews ") :])
    if journal == "Proceedings of the National Academy of Sciences":
        variants.append("Proceedings of the National Academy of Sciences of the United States of America")
    return variants


def sample(client: httpx.Client, journal: str) -> tuple[str, str]:
    """优先取有摘要的近期论文；没有则退回任意带 DOI 的记录。"""
    queries = []
    for name in name_variants(journal):
        queries.append(f'JOURNAL:"{name}" AND DOI:* AND HAS_ABSTRACT:Y')
        queries.append(f'JOURNAL:"{name}" AND DOI:*')
    for query in queries:
        try:
            response = client.get(
                EUROPEPMC,
                params={"query": query, "format": "json", "pageSize": 1, "resultType": "lite"},
                headers=HEADERS,
            )
            if response.status_code != 200:
                continue
            items = response.json().get("resultList", {}).get("result", [])
            if items and items[0].get("doi"):
                return items[0]["doi"], items[0].get("title") or ""
        except httpx.HTTPError:
            continue
        finally:
            time.sleep(0.25)
    return crossref_sample(client, journal)


def main(argv: list[str] | None = None) -> int:
    only_missing = "--only-missing" in sys.argv or "--only-missing" in (argv or [])
    rows = venues()
    previous = {}
    coverage_path = ROOT / "data/reports/abstracts/coverage.json"
    if only_missing and coverage_path.is_file():
        previous = {
            item["journal"]: item for item in json.loads(coverage_path.read_text(encoding="utf-8"))["results"]
        }
        rows = [
            (name, status) for name, status in rows if previous.get(name, {}).get("status") == "no_sample_doi"
        ]
    out = ROOT / "data/reports/abstracts"
    out.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(timeout=30, follow_redirects=True, headers=HEADERS)
    lookup = AbstractLookup(client=client)
    samples = []
    try:
        for index, (name, status) in enumerate(rows, start=1):
            doi, title = sample(client, name)
            samples.append({"journal": name, "watch_status": status, "doi": doi, "title": title})
            print(f"[{index}/{len(rows)}] sample {name} {doi}", flush=True)
        hits = lookup.fetch(item["doi"] for item in samples if item["doi"])
    finally:
        client.close()
    for item in samples:
        doi = item["doi"].removeprefix("https://doi.org/").lower() if item["doi"] else ""
        hit = hits.get(doi)
        if not doi:
            item.update({"status": "no_sample_doi", "source": "", "chars": 0})
        elif hit is None:
            item.update({"status": "none", "source": "", "chars": 0})
        else:
            item.update({"status": hit.status, "source": hit.source, "chars": len(hit.text)})
    counts: dict[str, int] = {}
    for item in samples:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    merged = dict(previous)
    merged.update({item["journal"]: item for item in samples})
    ordered = [merged[name] for name, _ in venues() if name in merged]
    counts = {}
    for item in ordered:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    samples = ordered
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "total": len(samples),
        "by_status": counts,
        "results": samples,
    }
    (out / "coverage.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [
        "# 逐刊摘要获取检查",
        "",
        f"生成时间：{payload['generated_at']}",
        "",
        "| 期刊 | 状态 | 来源 | 字符数 | 抽样 DOI |",
        "|---|---|---|---:|---|",
    ]
    for item in samples:
        lines.append(
            "| {journal} | {status} | {source} | {chars} | {doi} |".format(
                journal=item["journal"],
                status=item["status"],
                source=item["source"] or "-",
                chars=item["chars"],
                doi=item["doi"] or "-",
            )
        )
    lines += ["", "计数：" + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))]
    (out / "coverage.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(counts, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
