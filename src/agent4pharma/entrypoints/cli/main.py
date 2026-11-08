"""CLI entrypoint: parse, compose, dispatch and close resources."""

import json
import sys

from agent4pharma.composition import open_application_storage
from agent4pharma.entrypoints.cli.argument_parser import build_parser
from agent4pharma.entrypoints.cli.discovery_commands import execute
from agent4pharma.entrypoints.cli.source_directory_commands import catalog_command, registry_command
from agent4pharma.runtime.environment import load_model_environment, load_wechat_environment
from agent4pharma.runtime.settings import ApplicationSettings


def main(argv: list[str] | None = None) -> None:
    """解析参数、装配依赖并分派子命令；错误输出到 stderr，运行资源在退出前关闭。"""
    args = build_parser().parse_args(argv)
    try:
        load_model_environment(args.config.resolve().parent.parent / ".env")
        if args.command == "browser-paper-mcp":
            from agent4pharma.entrypoints.mcp.browser_paper_server import serve_browser_paper_mcp

            serve_browser_paper_mcp(args.root, args.run, args.record)
            return
        if args.command in {"production-mcp", "production-worker"}:
            if args.command == "production-mcp":
                from agent4pharma.entrypoints.mcp.production_server import serve_production_mcp

                serve_production_mcp(args.root, args.task, args.lease)
            else:
                from agent4pharma.composition import production_worker

                production_worker(args.root, args.task, args.lease)
            return
        if args.command == "investigation-mcp":
            from agent4pharma.entrypoints.mcp.investigation_server import serve_investigation_mcp

            serve_investigation_mcp(args.root, args.job)
            return
        if args.command == "paper-download":
            from agent4pharma.entrypoints.cli.paper_download_commands import paper_download_command

            raise SystemExit(paper_download_command(args, args.config.resolve().parent.parent))
        config = ApplicationSettings.load(args.config)
        if args.command == "paper-selection":
            from agent4pharma.composition import paper_selection_status, screen_batch

            if not args.status:
                screen_batch(
                    config,
                    args.batch,
                    abstract_budget=args.abstract_budget,
                    download_budget=args.download_budget,
                    retry_downloads=args.retry_downloads,
                    retry_models=args.retry_models,
                )
            print(
                json.dumps(paper_selection_status(config.data_dir, args.batch), ensure_ascii=False, indent=2)
            )
            return
        if args.command == "production":
            from agent4pharma.entrypoints.cli.production_commands import production_command

            production_command(args, config)
            return
        if args.command == "workbench":
            load_wechat_environment(args.config.resolve().parent.parent / ".env")
            from agent4pharma.entrypoints.web.server import run_workbench

            run_workbench(args.config.resolve(), config, args.port)
            return
        store = open_application_storage(config.data_dir)
        try:
            if args.command == "registry":
                registry_command(args, store)
            elif args.command == "acquisition":
                from agent4pharma.entrypoints.cli.acquisition_commands import acquisition_command

                acquisition_command(args, config, store)
            elif args.command == "catalog":
                catalog_command(args, store)
            elif args.command == "report":
                print(json.dumps(store.write_report(args.batch), ensure_ascii=False, indent=2))
            elif args.command == "screen":
                from agent4pharma.composition import paper_selection_status, screen_batch

                selection = screen_batch(config, args.batch)
                result = store.write_report(args.batch)
                result["paper_selection"] = paper_selection_status(config.data_dir, args.batch)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                if selection.selection_method == "local_decider_v1":
                    if selection.phase != "fulltexts":
                        raise SystemExit(2)
                elif selection.phase == "abstracts" or any(
                    i.state in {"shortlisted", "abstract_failed", "fulltext_failed"} for i in selection.items
                ):
                    raise SystemExit(2)
            else:
                execute(args, config, store)
        finally:
            store.close()
    except (ValueError, KeyError, RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
