"""只读检查已配置RSS作者单位覆盖；--live显式联网，不更新来源、运行或模型结果。"""

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from xml.etree.ElementTree import ParseError

from pydantic import BaseModel, Field

from agent4pharma.infrastructure.connectors.rss_feed import RssPageFetcher, parse_feed
from agent4pharma.infrastructure.persistence.sqlite.collection_plan_repository import CollectionPlanRepository
from agent4pharma.shared.clock import utcnow


class FeedAffiliationAudit(BaseModel):
    """本次真实Feed快照的覆盖统计；无法读取与成功无单位字段分别表示。"""

    source_id: str
    name: str
    url: str
    status: str
    entries: int = 0
    entries_with_affiliations: int = 0
    fields: list[str] = Field(default_factory=list)
    sha256: str = ""
    error: str = ""


def inspect_feed(source_id: str, name: str, url: str, output: Path) -> FeedAffiliationAudit:
    """获取一次当前Feed，保存原件并运行正式提取器，不跟进论文网页。"""
    result = FeedAffiliationAudit(source_id=source_id, name=name, url=url, status="failed")
    try:
        document = RssPageFetcher(max_bytes=4_000_000, timeout=15).fetch(url)
        if document.http_status != 200:
            result.error = "HTTP " + str(document.http_status)
            return result
        records = parse_feed(document.body, document.url, source_id)
        result.status = "success"
        result.entries = len(records)
        result.entries_with_affiliations = sum(bool(r.metadata["affiliations"]) for r in records)
        result.fields = sorted({e["field"] for r in records for e in r.metadata["affiliation_evidence"]})
        result.sha256 = hashlib.sha256(document.body).hexdigest()
        (output / (result.sha256 + ".xml")).write_bytes(document.body)
    except (OSError, ValueError, ParseError) as exc:
        result.error = type(exc).__name__
    return result


def main() -> None:
    """显式--live才发请求，报告写独立目录，不改生产计划或历史资料。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("data/reports/rss-affiliations"))
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("真实来源检查需显式 --live")
    plans = CollectionPlanRepository(args.root).list_plans(enabled_only=True)
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        pending = [
            pool.submit(inspect_feed, p.source_id, p.name, e.url, args.output)
            for p in plans
            for e in p.entries
            if e.kind == "rss"
        ]
        for future in as_completed(pending):
            row = future.result()
            rows.append(row)
            print(
                json.dumps(
                    {
                        "name": row.name,
                        "status": row.status,
                        "entries": row.entries,
                        "with_affiliations": row.entries_with_affiliations,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    payload = {"checked_at": utcnow(), "feeds": [r.model_dump(mode="json") for r in rows]}
    (args.output / "audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "feeds": len(rows),
                "successful": sum(r.status == "success" for r in rows),
                "entries": sum(r.entries for r in rows),
                "with_affiliations": sum(r.entries_with_affiliations for r in rows),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
