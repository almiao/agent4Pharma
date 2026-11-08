"""Opt-in live acquisition probe: one page per source, no model calls."""

import argparse
import json
from datetime import date
from pathlib import Path

from agent4pharma.composition import build_daily_literature_discovery
from agent4pharma.infrastructure.persistence.sqlite.application_storage import SqliteApplicationStorage
from agent4pharma.runtime.settings import ApplicationSettings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--date", type=date.fromisoformat, default=date(2026, 10, 27))
    args = parser.parse_args()
    config = ApplicationSettings.load(Path("config/discovery.toml"))
    config.data_dir = config.data_dir / "smoke"
    config.initial_days = 1
    config.overlap_days = 0
    config.page_budget = args.pages
    store = SqliteApplicationStorage(config.data_dir)
    workflow = build_daily_literature_discovery(config, store)
    try:
        report = workflow.run(args.date)
        print(
            json.dumps(
                {
                    "batch_id": report["batch_id"],
                    "status": report["status"],
                    "materials": report["unique_materials"],
                    "selected": len(report["candidates"]),
                    "sources": [
                        {
                            "source": j["request"]["source"],
                            "status": j["status"],
                            "progress": j["result"],
                            "error": j["error"],
                        }
                        for j in report["sources"]
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        workflow.close()
        store.close()


if __name__ == "__main__":
    main()
