"""稿件编辑与事实核验共用冻结返修额度；技术重试和通过不计返修。"""

from typing import Literal

from agent4pharma.modules.editorial.commission_contracts import Story
from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.workflows.production_dependencies import ProductionContext, ProductionUnit


def can_queue_revision(
    work: ProductionUnit, task: ProductionTask, story: Story, kind: Literal["reader", "fact"]
) -> bool:
    """在保存本轮报告前判断能否再排一次作者修订，避免两个评审各自重复放行。

    新任务由limits冻结总额；未声明总额的任务使用阅读一次、事实两次的宿主默认上限。
    统计实际排队的作者修订意图；作者技术重试的父任务仍为作者，不重复计数。
    额度用尽的报告、独立复议和通过报告都不产生修订意图。
    """
    context = ProductionContext.model_validate_json(task.input_json)
    if context.draft and any(
        p.draft_id == context.draft.id and p.edit_pending for p in work.releases.packages()
    ):
        return False
    if task.limits.max_revision_rounds is not None:
        tasks = {row.id: row for row in work.tasks.list()}
        requested = sum(
            row.story_id == story.id
            and row.role == "writer"
            and row.phase in {"", "draft-writing"}
            and not (row.parent_task_id in tasks and tasks[row.parent_task_id].role == "writer")
            and ProductionContext.model_validate_json(row.input_json).draft is not None
            for row in tasks.values()
        )
        return requested < task.limits.max_revision_rounds
    reading = sum(
        r.story_id == story.id and r.submission.decision == "changes_requested"
        for r in work.reviews.reader_list()
    )
    factual = sum(
        r.story_id == story.id and r.submission.decision == "changes_requested" for r in work.reviews.list()
    )
    return reading < 1 if kind == "reader" else factual < 2
