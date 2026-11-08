"""人工推荐后的有界制作链；不再做下载后的推荐复筛，后续角色保留退回权限。"""

from collections.abc import Callable

from agent4pharma.runtime.production_execution import ProductionLimits, ProductionTask
from agent4pharma.workflows.production_workbench import ProductionWorkbench


def continue_imported_paper(
    prepare_candidate: Callable[[], str],
    production: ProductionWorkbench,
    execute: Callable[[str], ProductionTask],
    progress: Callable[[str, str, str], None],
) -> None:
    """校验人工推荐后持续推进制作链；失败停下，不绕过主编/审核或向外发布。

    文件身份及人工推荐由准备端口校验；生产端口负责幂等任务和租约。
    只推进本论文派生的选题，不领取其他选题任务。
    """
    candidate_id = prepare_candidate()
    if not candidate_id:
        raise ValueError("人工推荐未形成有效制作输入")
    task = production.start([candidate_id], ProductionLimits.for_new_task())
    task = production.execution.request_automatic(task.id)
    visited = set()
    while True:
        if task.id in visited:
            raise ValueError("制作后继出现循环")
        visited.add(task.id)
        role = task.role
        current = task
        if current.status in {"paused", "cancelled"}:
            progress("waiting", current.id, "制作已暂停或停止，请在制作任务中显式继续")
            return
        if current.status in {"failed", "blocked"}:
            progress("failed", current.id, current.error or "上次执行失败，请重试此阶段")
            return
        progress(task.role, task.id, "")
        result = task if task.status == "succeeded" else execute(task.id)
        if result.status in {"paused", "cancelled"}:
            progress("waiting", result.id, "制作已暂停或停止，请在制作任务中显式继续")
            return
        if result.status != "succeeded":
            progress("failed", result.id, result.error or "角色任务未完成，请查看任务详情")
            return
        task = production.next_task(result)
        if task is None:
            if role == "reviewer" and any(
                s.id == result.story_id and s.status == "deferred" for s in production.snapshot().stories
            ):
                progress("failed", result.id, "编辑核验未通过或返工次数耗尽，本轮制作已结束")
            else:
                progress("succeeded", result.id, "")
            return
