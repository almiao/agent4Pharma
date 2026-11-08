"""将明确指定的本地采集归档汇为新批次；不联网、不执行模型，不覆盖历史。"""

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from agent4pharma.composition import prepare_discovery_config
from agent4pharma.infrastructure.persistence.sqlite.archive_batch_repository import ArchiveBatchRepository
from agent4pharma.infrastructure.persistence.sqlite.coarse_screening import assess_coarse
from agent4pharma.infrastructure.persistence.sqlite.management_sources import ManagedSourceRepository
from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction
from agent4pharma.modules.discovery.triage_contracts import TriagePolicy
from agent4pharma.modules.sources.management import SourceEdit
from agent4pharma.modules.sources.source_categories import source_category
from agent4pharma.runtime.locking import worker_lock


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--archive", type=Path, action="append", default=[])
    parser.add_argument("--batch", required=True)
    parser.add_argument("--source-edits", type=Path, help="明确复核的来源 ID→SourceEdit JSON；可省略")
    args = parser.parse_args()
    root = args.root.resolve()
    repository = ArchiveBatchRepository(root)
    with worker_lock(root):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = root / "backups" / f"before-archive-consolidation-{stamp}.sqlite3"
        repository.backup(backup)
        catalog = ManagedSourceRepository(root)
        if args.source_edits:
            for source_id, change in json.loads(args.source_edits.read_text(encoding="utf-8")).items():
                catalog.save_source(SourceEdit.model_validate(change), source_id)
        config = repository.latest_config()
        config.policy_version = "archive-ai-biology-v1"
        config.screening_json = ""
        config.intelligence_policy_ref = ""
        if config.triage.enabled:
            config.triage.policy_json = TriagePolicy().model_dump_json()
        prepare_discovery_config(config)
        result = repository.consolidate(args.batch, args.archive, config)
        with transaction(root) as db:
            assess_coarse(db, result.batch_id, config)
        unclassified = [
            s.id
            for s in catalog.list_sources(limit=200)
            if source_category(s.id, source_kind=s.source_kind, url=s.homepage_url) == "unknown"
        ]
        print(
            json.dumps(
                {**asdict(result), "backup": str(backup), "unclassified": unclassified},
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
