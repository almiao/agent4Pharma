"""准备真实返修输入的隔离回归；只有显式--execute-model才执行一次作者，不执行后继或交付。"""

import argparse
import difflib
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from agent4pharma.composition import build_production_workbench, open_application_storage
from agent4pharma.infrastructure.llm.production_runner import DshProductionRunner
from agent4pharma.infrastructure.persistence.paper_download_repository import PaperDownloadRepository
from agent4pharma.infrastructure.persistence.sqlite.article_regression_repository import ObservationRepository
from agent4pharma.infrastructure.persistence.sqlite.production_unit import production_unit
from agent4pharma.modules.content.revision_workspace import RevisionWorkspace
from agent4pharma.runtime.context_window_policy import RevisionWindowPolicy
from agent4pharma.runtime.environment import load_model_environment
from agent4pharma.runtime.production_lifecycle_cost import lifecycle_task_ids, summarize_lifecycle_cost
from agent4pharma.runtime.workflow_observation import read_observations
from agent4pharma.shared.clock import utcnow
from agent4pharma.workflows.newsroom_skills import replace_skill_resources
from agent4pharma.workflows.production_dependencies import ProductionContext
from agent4pharma.workflows.production_evidence import task_evidence
from agent4pharma.workflows.production_stage_spec import compile_context_spec
from agent4pharma.workflows.production_tool_profile import production_tool_names
from agent4pharma.workflows.revision_context import (
    REVISION_RESOURCE,
    WINDOW_INSTRUCTIONS,
    WORKSPACE_INSTRUCTIONS,
    is_patch_revision,
)


def prepare(source: Path, task_id: str, output: Path) -> str:
    source, output = source.resolve(), output.resolve()
    if source == output or source.is_relative_to(output) or output.exists():
        raise ValueError("隔离输出必须是新的独立目录，不能覆盖来源或已有实验")
    with production_unit(source, read_only=True) as work:
        original = work.tasks.get(task_id)
        drafts = [draft for draft in work.drafts.list() if draft.story_id == original.story_id]
        reviews = [review for review in work.reviews.list() if review.story_id == original.story_id]
        readers = [review for review in work.reviews.reader_list() if review.story_id == original.story_id]
    if not is_patch_revision(original):
        raise ValueError("只接受已有准确返修任务")
    context = ProductionContext.model_validate_json(original.input_json)
    output.mkdir(parents=True)
    open_application_storage(output).close()
    instructions = WORKSPACE_INSTRUCTIONS + WINDOW_INSTRUCTIONS
    bundle = replace_skill_resources(original.newsroom_skills, {REVISION_RESOURCE: instructions})
    prompt = compile_context_spec(
        instructions, "draft-writing", revision=True, contract="node-context-v2"
    ).prompt
    identity = "production-replay-" + uuid4().hex
    task = original.model_copy(
        update={
            "id": identity,
            "actor_id": "writer:" + identity,
            "status": "queued",
            "attempt": 0,
            "lease_token": "",
            "lease_until": "",
            "parent_task_id": "",
            "auto_run": False,
            "context_contract": "node-context-v2",
            "revision_window_policy": RevisionWindowPolicy(),
            "context_tools": (),
            "newsroom_skills": bundle,
            "prompt": prompt,
            "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest(),
            "required_resources": (REVISION_RESOURCE, "standards/scientific-evidence.md"),
            "skill_reads": (),
            "web_search_enabled": False,
            "document_reads": {},
            "reads": 0,
            "citation_lookups": (),
            "revision_workspace_json": "",
            "result_ref": "",
            "error": "",
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
    )
    task = task.model_copy(update={"context_tools": production_tool_names(task)})
    with production_unit(output) as work:
        work.stories.save(
            context.story.model_copy(update={"status": "active", "revision": task.expected_revision}), 0
        )
        # 保留完整版本序列与外键，不能用只复制当前稿的假库验证正式交接。
        for draft in drafts:
            work.drafts.save(draft)
        for review in reviews:
            work.reviews.save(review)
        for reader in readers:
            work.reviews.save_reader(reader)
        for evidence in task_evidence(task):
            work.drafts.add_evidence(task.story_id, evidence)
        work.tasks.save(task)
    documents = {e.document.id for e in task_evidence(task) if e.document}
    ledger = PaperDownloadRepository(source)
    for record in ledger.results():
        if record.id not in documents:
            continue
        verified = ledger.verified(record.request)
        if verified is None or verified.id != record.id:
            raise ValueError("原回归文档的身份校验失效")
        destination = output / "paper-downloads" / "files" / (record.sha256 + ".pdf")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(record.file, destination)
        PaperDownloadRepository(output).save(record.model_copy(update={"file": str(destination)}))
    # 复用已显式启动的资料服务，不启动/下载模型，也不复制密钥或DSH会话。
    material_service = source / "services" / "mineru"
    if material_service.is_dir():
        link = output / "services" / "mineru"
        link.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(material_service)],
                check=True,
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            link.symlink_to(material_service, target_is_directory=True)
    metadata = {
        "source_task": original.id,
        "source_task_hash": hashlib.sha256(original.model_dump_json().encode()).hexdigest(),
        "task_id": task.id,
        "source_root": str(source),
        "binding": "原任务冻结输入及已经保存的资料增量；新协议/提示词/工具为实验变量；不修改原任务",
        "model": task.model,
        "limits": task.limits.model_dump(),
        "auto_run": False,
        "window_policy": task.revision_window_policy.model_dump() if task.revision_window_policy else None,
    }
    (output / "replay.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return identity


def report(output: Path, identity: str) -> dict:
    with production_unit(output, read_only=True) as work:
        task = work.tasks.get(identity)
        context = ProductionContext.model_validate_json(task.input_json)
        body = work.drafts.get(task.result_ref).submission.body if task.result_ref else ""
        tasks = work.tasks.list()
        ids = lifecycle_task_ids(tasks, identity)
        result_refs = {t.result_ref for t in tasks if t.id in ids}
        reviews = [r for r in [*work.reviews.reader_list(), *work.reviews.list()] if r.id in result_refs]
    events = list(read_observations(ObservationRepository(output), [identity], ()))
    usages = [json.loads(e.output_json) for e in events if e.operation == "token_usage" and e.output_json]
    calls = []
    previous_output = 0
    for usage in usages:
        if usage["requests"] <= len(calls):
            continue
        calls.append(
            {
                "request": usage["requests"],
                "input_tokens": usage["last_input_tokens"],
                "output_tokens": usage["output_tokens"] - previous_output,
            }
        )
        previous_output = usage["output_tokens"]
    workspace = (
        RevisionWorkspace.model_validate_json(task.revision_workspace_json)
        if task.revision_workspace_json
        else None
    )
    summary = {
        "task_id": identity,
        "status": task.status,
        "error": task.error,
        "candidate_versions": workspace.version if workspace else 0,
        "result_ref": task.result_ref,
        "usage": usages[-1] if usages else None,
        "model_calls": calls,
        "max_single_output_tokens": max((call["output_tokens"] for call in calls), default=0),
        "tool_actions": [
            {"operation": e.operation, "status": e.status}
            for e in events
            if e.execution and e.execution.kind == "mcp"
        ],
        "executed_successor": any(t.id != identity and t.id in ids and t.attempt for t in tasks),
        "review_outcomes": [
            {
                "report_id": r.id,
                "draft_id": r.draft_id,
                "decision": r.submission.decision,
                "open_issue_ids": [i.id for i in r.submission.issues],
                "closed_issue_ids": getattr(r.submission, "closed_issue_ids", []),
            }
            for r in reviews
        ],
    }
    all_events = {
        key: list(read_observations(ObservationRepository(output), [key], ("token_usage",))) for key in ids
    }
    (output / "lifecycle.json").write_text(
        summarize_lifecycle_cost(tasks, all_events, identity).model_dump_json(indent=2), encoding="utf-8"
    )
    (output / "reviews.json").write_text(
        json.dumps([r.model_dump(mode="json") for r in reviews], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if body:
        diff = "".join(
            difflib.unified_diff(
                context.draft.submission.body.splitlines(keepends=True),
                body.splitlines(keepends=True),
                fromfile="original",
                tofile="candidate",
            )
        )
        (output / "revision.diff").write_text(diff, encoding="utf-8")
        (output / "article.md").write_text(body, encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("data"))
    parser.add_argument("--source-task")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--execute-model", action="store_true")
    parser.add_argument("--execute-followup", help="仅显式执行本实验链中指定的一个排队作者返修")
    parser.add_argument(
        "--execute-review", action="store_true", help="显式执行最多两个独立审核节点，不自动返修或制作"
    )
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    metadata = args.output_root / "replay.json"
    if metadata.exists():
        identity = json.loads(metadata.read_text(encoding="utf-8"))["task_id"]
    elif args.source_task and not args.report_only:
        identity = prepare(args.source_root, args.source_task, args.output_root)
    else:
        raise ValueError("先指定source-task准备隔离输入")
    if args.execute_model or args.execute_review or args.execute_followup:
        if args.report_only:
            raise ValueError("report-only禁止执行模型")
        load_model_environment(Path(__file__).resolve().parents[1] / ".env")
        app = build_production_workbench(args.output_root)
    if args.execute_model:
        if app.execution.get(identity).status != "queued":
            raise ValueError("一次实验仅执行一次作者任务，失败不自动重试")
        app.execution.execute(identity, DshProductionRunner(args.output_root, app.execution.is_current))
    review_parent = identity
    if args.execute_followup:
        with production_unit(args.output_root, read_only=True) as work:
            selected = work.tasks.get(args.execute_followup)
            ids = lifecycle_task_ids(work.tasks.list(), identity)
        if (
            selected.id not in ids
            or selected.id == identity
            or selected.phase != "draft-writing"
            or selected.status != "queued"
        ):
            raise ValueError("只能执行本实验链中准确排队的后续作者返修")
        app.execution.execute(selected.id, DshProductionRunner(args.output_root, app.execution.is_current))
        review_parent = selected.id
    if args.execute_review:
        if app.execution.get(review_parent).status != "succeeded":
            raise ValueError("作者必须先成功提交，才能显式执行独立审核")
        parent = review_parent
        for _ in range(2):
            with production_unit(args.output_root, read_only=True) as work:
                children = [t for t in work.tasks.list() if t.parent_task_id == parent]
            if len(children) != 1 or children[0].phase not in {"reading-review", "fact-check"}:
                break
            child = children[0]
            if child.status == "queued":
                app.execution.execute(
                    child.id, DshProductionRunner(args.output_root, app.execution.is_current)
                )
            if app.execution.get(child.id).status != "succeeded":
                break
            parent = child.id
    result = report(args.output_root, identity)
    print(
        json.dumps(
            {key: value for key, value in result.items() if key not in {"tool_actions", "model_calls"}},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
