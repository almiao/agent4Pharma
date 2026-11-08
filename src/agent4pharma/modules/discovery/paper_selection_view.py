"""摘要与全文清单的轻量投影；不向列表传输冻结提示词、全部摘要或任何会话信息。"""

from collections import Counter
from collections.abc import Sequence

from pydantic import JsonValue

from agent4pharma.modules.discovery.intelligence_contracts import IntelligenceTask
from agent4pharma.modules.discovery.news_preferences import match_priority_institutions
from agent4pharma.modules.discovery.paper_selection import PaperSelectionRun


def selection_view(
    run: PaperSelectionRun,
    tasks: Sequence[IntelligenceTask] = (),
    *,
    visible_record_ids: frozenset[str] | None = None,
) -> dict[str, JsonValue]:
    """返回有明确运行身份、顺序、下载阻断和可行动待办的只读投影。"""
    # 只读投影实时提交，不提前冻结排名或改写运行快照。
    # 只改条目顶层的展示状态；冻结请求、策略和正文仅读取，不深拷贝整批材料。
    run = run.model_copy(update={"items": [item.model_copy() for item in run.items]})
    by_record = {item.request.evidence[0].id: item for item in run.items}
    running = set()
    for task in tasks:
        if task.request.selection_ref != run.id or task.request.stage != "abstract":
            continue
        item = by_record.get(task.request.evidence[0].id)
        if item is None or run.phase != "abstracts":
            continue
        if (
            task.status == "complete"
            and task.result is not None
            and task.result.priority_relevance is not None
        ):
            item.state = "eligible" if task.result.disposition == "recommend" else "abstract_excluded"
            item.reason = task.result.reason
            item.relevance_score = task.result.priority_relevance
        elif task.status in {"failed", "interrupted"}:
            item.state = "abstract_failed"
            item.reason = "摘要技术失败，需续跑处理"
        elif task.status == "running":
            running.add(task.request.evidence[0].id)
    counts = Counter(item.state for item in run.items)
    completed = sum(item.state not in {"awaiting_abstract", "abstract_failed"} for item in run.items)
    if run.selection_method == "local_decider_v1":
        completed = sum(i.local_decision is not None or i.state == "abstract_missing" for i in run.items)
    items = []
    for item in sorted(run.items, key=lambda i: (i.rank is None, i.rank or 0, i.request.evidence[0].id)):
        primary = item.request.evidence[0]
        if visible_record_ids is not None and primary.id not in visible_record_ids:
            continue
        todo = ""
        if item.state == "manual_required":
            todo = "完成账号或出版商认证；若该入口仍不支持，请人工取得主文 PDF 并导入后续跑。"
        elif item.state == "unavailable":
            todo = "本次缺少 DOI、入口或权益，已让路；核实资料标识或使用有权访问的途径取得主文。"
        elif item.state == "download_failed":
            todo = "查看本次获取原因；等待冷却或处理失败后显式续跑，系统不会持续重试。"
        if item.state in {"abstract_failed", "fulltext_failed"}:
            todo = "模型技术失败最多自动尝试两次；核对错误后显式重试，不转为业务排除。"
        items.append(
            {
                "id": run.id + ":" + primary.id,
                "run_id": run.id,
                "record_id": primary.id,
                "title": primary.title,
                "source_id": item.source_id,
                "journal": item.journal,
                "affiliations": item.affiliations,
                "priority_institutions": match_priority_institutions(
                    item.affiliations, run.institution_preferences
                ),
                "download_database": {
                    "10.1038": "Nature（自然杂志）",
                    "10.1007": "SpringerLink",
                    "10.1016": "Science Direct",
                }.get(primary.doi.split("/", 1)[0].lower(), ""),
                "download_database_basis": "DOI 前缀对应出版平台；入口可用性需人工确认",
                "url": primary.url,
                "doi": primary.doi,
                "state": "abstract_running" if primary.id in running else item.state,
                "rank": item.rank,
                "local_rank": item.local_rank,
                "local_margin": item.local_margin,
                "local_route": item.local_decision.choice if item.local_decision else None,
                "reason": item.selection_reason or item.reason,
                "fulltext_reason": item.fulltext_reason,
                "continuation_status": item.continuation_status,
                "continuation_task_id": item.continuation_task_id,
                "continuation_error": item.continuation_error,
                "relevance_score": item.relevance_score,
                "abstract_task_id": item.abstract_task_id,
                "fulltext_task_id": item.fulltext_task_id,
                "download_id": item.download_id,
                "manual_selected_at": item.manual_selected_at,
                "can_associate_pdf": run.phase == "fulltexts" and not run.paused,
                "human_recommendation": (
                    item.human_recommendation.model_dump(mode="json") if item.human_recommendation else None
                ),
                "pdf_imported": bool(item.download_id)
                and item.state in {"shortlisted", "fulltext_failed", "complete"},
                "can_download": run.phase == "fulltexts"
                and bool(item.rank and 1 <= item.rank <= run.shortlist_limit),
                "todo": todo,
            }
        )
    return {
        "id": run.id,
        "batch_id": run.batch_id,
        "phase": run.phase,
        "paused": run.paused,
        "selection_method": run.selection_method,
        "processing_stage": run.processing_stage,
        "review_progress": {
            "total": sum(bool(i.local_rank and i.local_rank <= run.review_pool_limit) for i in run.items),
            "completed": sum(
                bool(i.local_rank and i.local_rank <= run.review_pool_limit)
                and i.state not in {"review_pending", "review_failed"}
                for i in run.items
            ),
        },
        "revision": run.revision,
        "shortlist_limit": run.shortlist_limit,
        "paused_journals": run.paused_journals,
        "processed_record_ids": run.processed_record_ids,
        "paused_papers": [
            {
                "record_id": item.request.evidence[0].id,
                "title": item.request.evidence[0].title,
                "journal": item.journal,
            }
            for item in run.paused_items
        ],
        "review_pool_limit": run.review_pool_limit,
        "authority_journals": run.authority_journals,
        "comparison_record_ids": (
            [paper.record_id for paper in run.comparison_request.papers] if run.comparison_request else None
        ),
        "news_selection_version": run.news_selection_version,
        "comparison_transport_version": run.comparison_transport_version,
        "comparison_error": run.comparison_error,
        "comparison_result": run.comparison_result.model_dump(mode="json") if run.comparison_result else None,
        "counts": dict(counts),
        "progress": {
            "total": len(run.items),
            "completed": completed,
            "running": max(len(running), int(run.processing_stage != "idle")),
            "failed": counts["abstract_failed"] + counts["local_failed"] + counts["review_failed"],
            "remaining": len(run.items) - completed,
        },
        "items": items,
    }
