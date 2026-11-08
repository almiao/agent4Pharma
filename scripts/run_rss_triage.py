"""使用正式采集/本地预选用例重新读取全部已验证期刊 RSS，导出真实判别产物。"""

import csv
import json
from collections import Counter
from pathlib import Path

from agent4pharma.composition import (
    build_collection_plan_catalog,
    build_daily_literature_discovery,
    open_application_storage,
)
from agent4pharma.infrastructure.persistence.sqlite.rss_triage_repository import RssTriageRepository
from agent4pharma.runtime.settings import ApplicationSettings


def main() -> None:
    """不调用远程精筛或发行；所有预选通过固定本地模型处理且可幂等续跑。"""
    config = ApplicationSettings.load(Path("config/discovery.toml"))
    plans = [
        plan
        for plan in build_collection_plan_catalog(config.data_dir).list_plans(enabled_only=True)
        if plan.rss_profile and plan.acquisition_mode == "feed_metadata"
    ]
    if not plans or not config.triage.enabled:
        raise ValueError("没有已验证的 RSS 字段配置，或未启用本地模型")
    store = open_application_storage(config.data_dir)
    flow = build_daily_literature_discovery(
        config, store, configured_only=True, source_ids=[plan.source_id for plan in plans]
    )
    out = Path("data/reports/rss-triage")
    try:
        report = flow.run(config.yesterday())
        decisions = RssTriageRepository(config.data_dir).decisions(report["batch_id"])
        out.mkdir(parents=True, exist_ok=True)
        out.joinpath("batch.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        out.joinpath("decisions.json").write_text(
            json.dumps([row.model_dump() for row in decisions], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with out.joinpath("decisions.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            historical_three_way = any(row.result.probabilities.insufficient is not None for row in decisions)
            writer = csv.DictWriter(
                stream,
                fieldnames=[
                    "material_ref",
                    "journal",
                    "title",
                    "url",
                    "info",
                    "model_choice",
                    "route",
                    "p_analyze",
                    "p_exclude",
                    *(["p_insufficient"] if historical_three_way else []),
                    "input_tokens",
                    "decision_id",
                ],
            )
            writer.writeheader()
            for row in decisions:
                writer.writerow(
                    {
                        "material_ref": row.input.material_ref,
                        "journal": row.input.journal,
                        "title": row.input.title,
                        "url": row.input.url,
                        "info": row.input.information_status,
                        "model_choice": row.result.choice,
                        "route": row.route,
                        "p_analyze": row.result.probabilities.analyze,
                        "p_exclude": row.result.probabilities.exclude,
                        **(
                            {"p_insufficient": row.result.probabilities.insufficient}
                            if historical_three_way
                            else {}
                        ),
                        "input_tokens": row.result.input_tokens,
                        "decision_id": row.id,
                    }
                )
        summary = {
            "batch_id": report["batch_id"],
            "sources": len(plans),
            "materials": report["unique_materials"],
            "decisions": len(decisions),
            "routes": dict(Counter(row.route for row in decisions)),
            "choices": dict(Counter(row.result.choice for row in decisions)),
            "information": dict(Counter(row.input.information_status for row in decisions)),
            "awaiting_triage": report["awaiting_triage"],
            "coverage_status": report["coverage_status"],
            "awaiting_intelligence": report["awaiting_intelligence"],
            "model_revision": config.triage.model_revision,
            "remote_model_invoked": False,
        }
        out.joinpath("summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    finally:
        flow.close()
        store.close()


if __name__ == "__main__":
    main()
