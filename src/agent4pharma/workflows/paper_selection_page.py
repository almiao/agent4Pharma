"""精选页只读查询：目录、当前清单和同轮比较，不装载历史全池或运行模型。"""

from collections.abc import Callable, Sequence
from dataclasses import asdict
from typing import Protocol

from pydantic import JsonValue

from agent4pharma.modules.discovery.intelligence_contracts import IntelligenceTask
from agent4pharma.modules.discovery.paper_selection import PaperSelectionRun
from agent4pharma.modules.discovery.paper_selection_queries import PaperSelectionSummary
from agent4pharma.modules.discovery.paper_selection_view import selection_view


class SelectionPageStore(Protocol):
    """查询目录后仅打开被选中的准确运行。"""

    def summaries(self, batch_id: str = "") -> list[PaperSelectionSummary]:
        """返回轻量目录。"""
        ...

    def get(self, run_id: str) -> PaperSelectionRun:
        """读取指定运行最新修订。"""
        ...


def selection_page(
    store: SelectionPageStore,
    batch_id: str,
    run_id: str = "",
    tasks: Sequence[IntelligenceTask] = (),
    task_reader: Callable[[str], Sequence[IntelligenceTask]] | None = None,
) -> dict[str, JsonValue]:
    """投影下载建议、同轮比较与人工精选成员；历史身份不跨批次回退。

    无运行返回明确空目录；显式运行不存在或不属于批次时抛 KeyError，不改查其他运行。
    """
    summaries = store.summaries(batch_id)
    selected = next((item for item in summaries if item.id == run_id), None) if run_id else None
    if run_id and selected is None:
        raise KeyError("筛选运行不属于当前批次或不存在")
    selected = selected or (summaries[-1] if summaries else None)
    view = None
    if selected:
        run = store.get(selected.id)
        comparison = (
            {paper.record_id for paper in run.comparison_request.papers}
            if run.comparison_request
            else {
                item.request.evidence[0].id
                for item in run.items
                if item.local_rank is not None and item.local_rank <= run.review_pool_limit
            }
        )
        visible = comparison | {
            item.request.evidence[0].id
            for item in run.items
            if item.manual_selected_at or (item.rank is not None and 1 <= item.rank <= run.shortlist_limit)
        }
        if task_reader and run.phase == "abstracts":
            tasks = task_reader(run.id)
        view = selection_view(run, tasks, visible_record_ids=frozenset(visible))
    return {
        "selected": batch_id,
        "runs": [asdict(item) for item in summaries],
        "paper_selections": [view] if view else [],
    }
