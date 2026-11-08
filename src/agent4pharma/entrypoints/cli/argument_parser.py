"""Command-line grammar; no resource creation or business execution."""

import argparse
from datetime import date
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """构造命令行语法，不读取配置、不创建数据库或网络资源。"""
    parser = argparse.ArgumentParser(description="AI × Biology/Pharma 资料发现、论文精选与制作")
    parser.add_argument("--config", type=Path, default=Path("config/discovery.toml"))
    commands = parser.add_subparsers(dest="command", required=True)
    from agent4pharma.entrypoints.cli.production_commands import add_production_arguments

    add_production_arguments(commands)
    from agent4pharma.entrypoints.cli.paper_download_commands import add_paper_download_arguments

    add_paper_download_arguments(commands)
    browser_mcp = commands.add_parser(
        "browser-paper-mcp", help="仅为个位数清单中的绑定论文开放浏览器获取工具"
    )
    browser_mcp.add_argument("--root", type=Path, required=True)
    browser_mcp.add_argument("--run", required=True)
    browser_mcp.add_argument("--record", required=True)
    selection = commands.add_parser("paper-selection", help="摘要筛选、个位数全文清单及人工下载待办")
    selection.add_argument("--batch", required=True)
    selection.add_argument(
        "--abstract-budget", type=int, default=500, choices=range(0, 501), metavar="0..500"
    )
    selection.add_argument("--download-budget", type=int, default=0, choices=range(0, 3))
    selection.add_argument(
        "--retry-downloads", action="store_true", help="人工处理后显式重试受阻清单，不扩大清单"
    )
    selection.add_argument("--retry-models", action="store_true", help="显式重试达到两次技术失败上限的模型")
    selection.add_argument("--status", action="store_true", help="只读状态，不创建运行、不联网、不调用模型")
    run = commands.add_parser("run", help="Collect yesterday (or a date) and sample; resumable")
    run.add_argument("--date", type=date.fromisoformat)
    run.add_argument("--collect-only", action="store_true", help="仅采集和粗筛；语义判断保持待处理")
    schedule = commands.add_parser("schedule", help="Foreground 08:00 scheduler with catch-up")
    schedule.add_argument("--once", action="store_true", help="Process due work once and exit")
    schedule.add_argument("--collect-only", action="store_true")
    report = commands.add_parser("report")
    report.add_argument("batch")
    catalog = commands.add_parser(
        "catalog", help="Import or inspect venue directory; does not enable IF filtering"
    )
    catalog_commands = catalog.add_subparsers(dest="catalog_command", required=True)
    catalog_import = catalog_commands.add_parser("import")
    catalog_import.add_argument("--seed", type=Path, required=True, help="显式指定外部整理目录数据")
    catalog_import.add_argument("--inventory", type=Path, required=True, help="显式指定外部载体观察快照")
    catalog_list = catalog_commands.add_parser("list")
    catalog_list.add_argument("--status", choices=["watch", "candidate", "unreviewed"])
    catalog_list.add_argument("--limit", type=int, default=200)
    registry = commands.add_parser("registry", help="Shared source directory and isolated demo metadata")
    registry_commands = registry.add_subparsers(dest="registry_command", required=True)
    demo_import = registry_commands.add_parser("import-demo")
    demo_import.add_argument("--seed", type=Path, required=True, help="显式指定外部演示数据")
    registry_commands.add_parser("demo")
    workbench = commands.add_parser("workbench", help="本地来源/检索词/文章调查工作台")
    workbench.add_argument("--port", type=int, default=8765)
    investigate = commands.add_parser("investigation-mcp", help="内部：绑定文章调查的 stdio MCP")
    investigate.add_argument("--root", type=Path, required=True)
    investigate.add_argument("--job", required=True)
    review = commands.add_parser("screen", help="对已有批次续跑本地排序与商业整体比较")
    review.add_argument("batch")
    acquisition = commands.add_parser("acquisition", help="数据库配置驱动的通用媒体采集")
    acquisition_commands = acquisition.add_subparsers(dest="acquisition_command", required=True)
    acquisition_commands.add_parser("list", help="列出来源计划及启停状态")
    show = acquisition_commands.add_parser("get", help="读取单个数据库计划及当前版本")
    show.add_argument("source_id")
    save = acquisition_commands.add_parser("save", help="从标准输入校验并保存数据库计划")
    save.add_argument("--expected-hash", help="更新已有计划时必须提供读取到的版本")
    for action in ("enable", "disable"):
        change = acquisition_commands.add_parser(action, help="修改数据库计划启停状态")
        change.add_argument("source_id")
        change.add_argument("--expected-hash", required=True)
    resume = acquisition_commands.add_parser("resume", help="使用历史冻结配置续跑媒体批次")
    resume.add_argument("batch")
    resume.add_argument("--collect-only", action="store_true")
    for name in ("run", "schedule"):
        task = acquisition_commands.add_parser(name, help="执行通用配置来源，共用日常发现流程")
        task.add_argument("--collect-only", action="store_true")
        task.add_argument("--source", action="append", help="仅执行指定数据库来源 ID，可重复")
        if name == "run":
            task.add_argument("--date", type=date.fromisoformat)
        else:
            task.add_argument("--once", action="store_true")
    return parser
