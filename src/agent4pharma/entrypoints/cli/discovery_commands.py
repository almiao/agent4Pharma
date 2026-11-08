"""Foreground execution and scheduler CLI output."""

import json
import sys
import time
from argparse import Namespace

from agent4pharma.composition import build_daily_literature_discovery, paper_selection_automation_finished
from agent4pharma.runtime.scheduling import due_days
from agent4pharma.runtime.settings import ApplicationSettings


def execute(args: Namespace, config: ApplicationSettings, store) -> None:
    """执行 run/schedule 命令并输出实际结果；部分失败返回退出码 2，始终关闭流程资源。"""
    options = {
        "configured_only": getattr(args, "configured_only", False),
        "source_ids": getattr(args, "source", None),
        "frozen_batch": getattr(args, "resume_batch", None),
    }
    workflow = build_daily_literature_discovery(config, store, **options)
    config = workflow.config
    try:
        while True:
            days = (
                [args.date or config.yesterday()]
                if args.command == "run"
                else due_days(
                    config,
                    store,
                    collect_only=args.collect_only,
                    workflow_completed=lambda batch: paper_selection_automation_finished(
                        config.data_dir, batch
                    ),
                )
            )
            failed = False
            for day in days:
                result = workflow.run(day)
                selection = None
                if not args.collect_only:
                    from agent4pharma.composition import screen_batch

                    try:
                        selection = screen_batch(config, result["batch_id"])
                    except (ValueError, RuntimeError) as exc:
                        print("Intelligence: " + str(exc), file=sys.stderr, flush=True)
                        failed = True
                    result = store.write_report(result["batch_id"])
                    failed |= (
                        selection is None
                        or selection.phase == "abstracts"
                        or any(i.state in {"shortlisted", "fulltext_failed"} for i in selection.items)
                    )
                print(
                    json.dumps(
                        {
                            "batch_id": result["batch_id"],
                            "status": result["status"],
                            "materials": result["unique_materials"],
                            "selected": len(result["candidates"]),
                            "awaiting_intelligence": result.get("awaiting_intelligence", 0),
                            "selection_phase": selection.phase if selection else None,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                completion = result["coverage_status"]
                failed |= completion != "complete" or bool(result.get("awaiting_triage"))
            if args.command == "run" or args.once:
                if failed:
                    raise SystemExit(2)
                break
            # Retry partial runs with bounded cadence. The OS owns scheduler process startup.
            time.sleep(60)
            workflow.close()
            config = ApplicationSettings.load(args.config)
            workflow = build_daily_literature_discovery(config, store, **options)
    finally:
        workflow.close()
