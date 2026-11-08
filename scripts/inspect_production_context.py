"""离线检查准确任务的上下文记录；只读数据库，不领取租约或调用模型。"""

import argparse
import json
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.article_regression_repository import ObservationRepository
from agent4pharma.infrastructure.persistence.sqlite.production_unit import production_unit
from agent4pharma.runtime.context_records import canonical_payload
from agent4pharma.runtime.workflow_observation import read_observations
from agent4pharma.workflows.production_context_reports import inspect_task_context
from agent4pharma.workflows.production_working_sets import research_catalog, source_catalog, task_checkpoint


def main() -> None:
    """历史对照只计算同一绑定的目录/游标尺寸，不把模拟投影声明为真实模型请求。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--task", required=True)
    parser.add_argument("--attempt", type=int)
    parser.add_argument("--compare-frozen", action="store_true")
    args = parser.parse_args()
    sink = ObservationRepository(args.root)
    with production_unit(args.root, read_only=True) as work:
        task = work.tasks.get(args.task)
        attempt = args.attempt if args.attempt is not None else task.attempt
        report = inspect_task_context(task, sink, attempt).model_dump(mode="json")
        if args.compare_frozen:
            projected = task.model_copy(update={"context_contract": "node-context-v1", "context_tools": ()})
            sources = source_catalog(work, projected)
            checkpoint = task_checkpoint(projected)
            research = research_catalog(projected)
            source_total = len(canonical_payload(sources.model_dump(mode="json")))
            source_pages = 1
            page = sources
            while page.has_more:
                page = source_catalog(work, projected, page.next_cursor)
                source_total += len(canonical_payload(page.model_dump(mode="json")))
                source_pages += 1
            report["offline_projection"] = {
                "basis": "准确任务冻结输入与已保存研究；未运行模型，未修改原任务",
                "source_catalog_characters": len(canonical_payload(sources.model_dump(mode="json"))),
                "checkpoint_characters": len(canonical_payload(checkpoint.model_dump(mode="json"))),
                "research_catalog_characters": len(canonical_payload(research.model_dump(mode="json"))),
                "source_page_has_more": sources.has_more,
                "all_source_pages_characters": source_total,
                "source_pages": source_pages,
                "research_page_has_more": research.has_more,
            }
    old_sizes = []
    for event in read_observations(sink, [task.id], ("get_production_context",)):
        if event.execution and event.execution.attempt == attempt and event.output_json:
            payload = json.loads(event.output_json)
            if isinstance(payload, dict) and not payload.get("truncated"):
                old_sizes.append(len(canonical_payload(payload)))
    report["historical_whole_context_characters"] = old_sizes
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
