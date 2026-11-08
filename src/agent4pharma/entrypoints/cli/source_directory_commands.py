"""CLI output for source-directory commands."""

import json
from argparse import Namespace


def registry_command(args: Namespace, store) -> None:
    """导入或查询隔离的来源 demo，并输出真实关系投影与报告路径。"""
    from agent4pharma.modules.sources.directory_reports import write_demo_review

    result = store.import_demo(args.seed) if args.registry_command == "import-demo" else store.inspect_demo()
    output = store.root / "reports" / "source-demo.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key.endswith("count")}))
    print(f"Report: {output}")
    print(f"Review: {write_demo_review(store, result)}")


def catalog_command(args: Namespace, store) -> None:
    """导入或查询载体目录并输出报告；不启用来源采集或 IF 筛选。"""
    from agent4pharma.modules.sources.directory_reports import write_import_report

    if args.catalog_command == "import":
        result = store.import_catalog(args.seed, args.inventory)
        report_path = write_import_report(store, result)
        print(
            json.dumps(
                {
                    key: value
                    for key, value in result.items()
                    if key not in {"overlap_with_baseline", "additions_to_baseline"}
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print(f"Report: {report_path}")
    else:
        print(json.dumps(store.list_catalog(args.status, args.limit), ensure_ascii=False, indent=2))
