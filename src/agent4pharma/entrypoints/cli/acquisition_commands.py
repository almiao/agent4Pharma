"""通用采集的数据库配置管理与运行 CLI，不依赖站点配置文件。"""

import json
import sys
from argparse import Namespace
from datetime import date

from agent4pharma.composition import build_collection_plan_catalog, save_collection_plan
from agent4pharma.entrypoints.cli.discovery_commands import execute
from agent4pharma.runtime.settings import ApplicationSettings


def acquisition_command(args: Namespace, config: ApplicationSettings, store) -> None:
    """直接管理数据库计划；更新校验版本，运行复用既有发现用例。"""
    catalog = build_collection_plan_catalog(config.data_dir)
    action = args.acquisition_command
    if action == "list":
        plans = catalog.list_plans()
        print(json.dumps([p.model_dump(mode="json") for p in plans], ensure_ascii=False, indent=2))
    elif action in {"get", "save", "enable", "disable"}:
        if action == "save":
            definition = sys.stdin.read(1_000_001)
            if len(definition) > 1_000_000:
                raise ValueError("配置请求超过大小限制")
            plan = save_collection_plan(definition, config.data_dir, expected_hash=args.expected_hash)
        else:
            plan = catalog.get(args.source_id)
            if action != "get":
                plan = catalog.save(
                    plan.model_copy(update={"enabled": action == "enable"}), expected_hash=args.expected_hash
                )
        print(json.dumps({"content_hash": plan.fingerprint(), "plan": plan.model_dump()}, ensure_ascii=False))
    else:
        args.command, args.configured_only = args.acquisition_command, True
        if args.acquisition_command == "resume":
            args.command, args.resume_batch = "run", args.batch
            args.date = date.fromisoformat(store.job(args.batch)["request"]["day"])
        execute(args, config, store)
