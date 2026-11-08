"""按用户指定保留批次清理发现历史；先备份，再输出无引用文件清单供删除。"""

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.archive_batch_repository import ArchiveBatchRepository
from agent4pharma.infrastructure.persistence.sqlite.retained_batch_repository import RetainedBatchRepository
from agent4pharma.runtime.locking import worker_lock


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--keep-batch", action="append", required=True)
    parser.add_argument("--active-batch", help="与 --retry-batch 一起将补采结果并回此活动批次")
    parser.add_argument("--retry-batch")
    args = parser.parse_args()
    if bool(args.active_batch) != bool(args.retry_batch):
        raise ValueError("并回补采时须同时指定 active-batch 和 retry-batch")
    root = args.root.resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = root / "backups" / f"before-history-cleanup-{stamp}.sqlite3"
    with worker_lock(root):
        ArchiveBatchRepository(root).backup(backup)
        repository = RetainedBatchRepository(root)
        merged = repository.merge_retry(args.active_batch, args.retry_batch) if args.active_batch else 0
        result = repository.prune_history(args.keep_batch)
    report = {**asdict(result), "merged": merged, "backup": str(backup)}
    output = root / "reports" / "history-cleanup-20261031.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps({k: v for k, v in report.items() if k != "obsolete_files"}, ensure_ascii=False, indent=2)
    )
    print("obsolete_files", len(result.obsolete_files), "manifest", output)


if __name__ == "__main__":
    main()
