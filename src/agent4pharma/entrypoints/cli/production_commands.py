"""制作 CLI 协议；标准输入为结构化请求，模型执行需显式 --allow-model。"""

import argparse
import sys
from pathlib import Path

from agent4pharma.composition import (
    build_production_workbench,
    execute_production_task,
    open_application_storage,
)
from agent4pharma.runtime.production_execution import ProductionLimits
from agent4pharma.runtime.settings import ApplicationSettings


def add_production_arguments(commands: argparse._SubParsersAction) -> None:
    """注册制作及内部 MCP/worker 语法，不创建资源。"""
    for name in ("production-mcp", "production-worker"):
        internal = commands.add_parser(name, help="内部：绑定制作角色任务")
        internal.add_argument("--root", type=Path, required=True)
        internal.add_argument("--task", required=True)
        internal.add_argument("--lease", required=True)
    production = commands.add_parser("production", help="主编、内容、编辑与本地发行制作")
    actions = production.add_subparsers(dest="production_action", required=True)
    actions.add_parser("list")
    start = actions.add_parser("start", help="选正式候选并排队论文理解，不调用模型")
    start.add_argument("--candidate", action="append", required=True)
    for name in ("run", "continue"):
        run = actions.add_parser(name, help="显式授权后执行模型；continue 有界执行选题下游")
        run.add_argument("task")
        run.add_argument("--allow-model", action="store_true", required=True)
        run.add_argument("--max-tasks", type=int, default=4)
    export = actions.add_parser("export")
    export.add_argument("package")
    resume = actions.add_parser("resume", help="独立编辑复议，只排队")
    resume.add_argument("story")
    resume.add_argument("--role", choices=["reviewer"], default="reviewer")


def production_command(args: argparse.Namespace, config: ApplicationSettings) -> None:
    """迁移后装配窄业务服务；没有 --allow-model 的请求不发送模型调用。"""
    with_storage = open_application_storage(config.data_dir)
    with_storage.close()
    app = build_production_workbench(config.data_dir, config.llm.model, config.llm.base_url)
    action = args.production_action
    if action == "list":
        print(app.snapshot().model_dump_json(indent=2))
    elif action == "start":
        print(app.start(args.candidate, ProductionLimits.for_new_task()).model_dump_json(indent=2))
    elif action in {"run", "continue"}:
        if not 1 <= args.max_tasks <= 20:
            raise ValueError("每次执行任务上限须为 1..20")
        task_id = args.task
        for _ in range(args.max_tasks if action == "continue" else 1):
            result = execute_production_task(config.data_dir, task_id)
            print(result.model_dump_json(indent=2))
            if result.status != "succeeded":
                raise RuntimeError("制作任务未完成，请查看脱敏错误与阻断原因")
            next_task = app.next_task(result)
            if next_task is None:
                break
            task_id = next_task.id
    elif action == "export":
        body, _ = app.export_package(args.package)
        sys.stdout.write(body)
    elif action == "resume":
        print(app.resume(args.story, args.role, ProductionLimits.for_new_task()).model_dump_json(indent=2))
