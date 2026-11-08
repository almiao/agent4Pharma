"""按准确任务读取已提交业务产物，不用当前选题或后继任务代替历史输出。"""

from typing import Literal

from pydantic import BaseModel

from agent4pharma.modules.content.draft_contracts import DraftRevision
from agent4pharma.modules.editorial.commission_contracts import EditorialDecision
from agent4pharma.modules.publishing.release_contracts import ChannelPackage
from agent4pharma.modules.review.reader_review_contracts import ReaderReviewReport
from agent4pharma.modules.review.review_contracts import ReviewReport
from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.workflows.production_dependencies import ProductionUnit


class ProductionTaskArtifact(BaseModel):
    """任务输出的只读展示契约；内容仍使用所属业务模块的准确版本类型。"""

    kind: Literal["decision", "draft", "review", "reader_review", "package"]
    content: EditorialDecision | DraftRevision | ReviewReport | ReaderReviewReport | ChannelPackage


def production_task_artifact(work: ProductionUnit, task: ProductionTask) -> ProductionTaskArtifact | None:
    """只读取成功任务的显式结果引用；历史缺失返回未知，不读取最新版本补位。"""
    if task.status != "succeeded":
        return None
    if task.role == "editor":
        decision = next((row.decision for row in work.stories.decisions() if row.task_id == task.id), None)
        return ProductionTaskArtifact(kind="decision", content=decision) if decision else None
    if task.phase == "article-revision" or not task.result_ref:
        return None
    if task.role == "writer":
        artifact = next((row for row in work.drafts.list() if row.id == task.result_ref), None)
        kind = "draft"
    elif task.phase == "reading-review":
        artifact = next((row for row in work.reviews.reader_list() if row.id == task.result_ref), None)
        kind = "reader_review"
    elif task.role == "reviewer":
        artifact = next((row for row in work.reviews.list() if row.id == task.result_ref), None)
        kind = "review"
    elif task.role == "publisher":
        artifact = next((row for row in work.releases.packages() if row.id == task.result_ref), None)
        kind = "package"
    else:
        return None
    if artifact is None or artifact.story_id != task.story_id:
        return None
    return ProductionTaskArtifact(kind=kind, content=artifact)
