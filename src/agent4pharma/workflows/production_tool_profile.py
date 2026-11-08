"""冻结上下文契约对应的工具白名单；提示词与注册使用同一选择规则。"""

from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.workflows.revision_context import is_patch_revision


def production_tool_names(task: ProductionTask, *, bundled_reader: bool = False) -> tuple[str, ...]:
    """按任务冻结阶段返回具名工具，旧任务不自动取得新上下文协议。"""
    if task.context_contract and task.context_tools:
        return task.context_tools
    if task.context_contract == "node-context-v2" and is_patch_revision(task):
        return (
            *(("apply_revision_step",) if task.revision_window_policy else ()),
            "get_revision_context",
            "read_revision_candidate",
            "edit_revision_candidate",
            "respond_revision_issue",
            "restore_revision_candidate",
            "validate_revision_candidate",
            "submit_revision_candidate",
            "list_task_sources",
            "read_evidence",
            "read_document",
            "find_bound_evidence",
            "find_bound_evidence_batch",
            "read_newsroom_resource",
            "report_production_blocked",
            "list_research_items",
            "read_research_item",
        )
    compact_research = bool(task.context_contract) or (
        task.newsroom_skills is not None
        and task.newsroom_skills.context_policy in {"research-checkpoint-v2", "stage-working-set-v1"}
    )
    submission = {
        "editor": "submit_editorial_decision",
        "writer": "submit_draft",
        "reviewer": "submit_review",
        "publisher": "submit_channel_package",
    }[task.role]
    names = ["get_production_context", "report_production_blocked", submission]
    if task.phase == "article-revision":
        names = [
            "get_article_edit_context",
            "find_article_text",
            "submit_article_edit",
            "report_production_blocked",
        ]
        if task.web_search_enabled:
            names.extend(
                [
                    "read_evidence",
                    "read_document",
                    "find_bound_evidence",
                    "read_bound_source",
                    "find_paper_figures",
                    "inspect_paper_figures",
                ]
            )
    if task.role == "publisher":
        names.extend(["find_paper_figures", "inspect_paper_figures"])
    if task.newsroom_skills is not None:
        names.extend(["list_newsroom_skills", "read_newsroom_resource"])
    if task.phase != "article-revision" and task.role == "writer":
        names.extend(["search_local_materials", "read_bound_source"])
    if task.phase != "article-revision" and task.role in {"writer", "reviewer"}:
        names.append("find_bound_evidence")
        if not task.newsroom_policy or task.phase in {"draft-writing", "fact-check"}:
            names.append("read_document")
    if task.role == "editor" and task.phase == "topic-selection" and task.research_policy == "pitch-first-v1":
        names.append("find_bound_evidence")
        names.append("read_document")
    if task.role == "writer" and task.phase == "draft-writing" and task.research_policy == "pitch-first-v1":
        names.append("save_writing_research")
    if task.phase != "article-revision" and task.role in {"editor", "writer", "reviewer"}:
        names.append("read_evidence")
    if task.research_policy == "pitch-first-v1" and (task.role, task.phase) in {
        ("editor", "topic-selection"),
        ("writer", "draft-writing"),
    }:
        names.append("save_background_research")
    if task.phase == "reading-review":
        entry = (
            ("get_reader_review_context", "record_reader_observation")
            if bundled_reader
            else ("get_reader_draft", "save_reader_observation", "get_reader_brief")
        )
        names = [
            *entry,
            "submit_reader_review",
            "report_production_blocked",
            "list_newsroom_skills",
            "read_newsroom_resource",
        ]
        if task.revision_window_policy:
            names.append("read_reader_review")
    if "find_bound_evidence" in names and compact_research:
        names.append("find_bound_evidence_batch")
    if is_patch_revision(task):
        names.remove("submit_draft")
        names.extend(["get_revision_context", "submit_draft_revision"])
    if task.context_contract:
        if "get_production_context" in names:
            names.remove("get_production_context")
            if not is_patch_revision(task):
                names.append("get_stage_context")
        if task.phase not in {"reading-review", "article-revision"}:
            names.extend(["list_task_sources", "get_task_checkpoint"])
        if task.phase in {"topic-selection", "draft-writing", "fact-check"}:
            names.extend(["list_research_items", "read_research_item"])
    return tuple(dict.fromkeys(names))
