"""中间保存点的轻量目录；只列本任务实际保存的版本，不复制继承底稿。"""

from typing import Literal

from pydantic import BaseModel

from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.runtime.workflow_observation import ObservationSink
from agent4pharma.workflows.background_research import background_research_versions
from agent4pharma.workflows.writing_research import writing_research_versions


class ProductionIntermediateSummary(BaseModel):
    """可定位到原任务和准确版本的保存点，不批量返回底稿正文。"""

    id: str
    task_id: str
    kind: Literal["writing_research", "background_research", "reader_observation", "article_edit"]
    version: int = 0
    created_at: str = ""


def summarize_intermediates(
    tasks: list[ProductionTask], observations: ObservationSink | None = None
) -> list[ProductionIntermediateSummary]:
    """列出失败、运行和已完成任务的实际保存点；未知保存时间保持空。"""
    historical = {}
    missing = [
        task.id for task in tasks if task.reader_observation_json and not task.reader_observation_created_at
    ]
    if observations is not None and missing:
        for event in observations.list(missing, operations=("save_reader_observation",)):
            if event.status == "succeeded":
                historical.setdefault(event.subject_ref, event.created_at)
    result = []
    for task in tasks:
        for kind, versions in (
            ("writing_research", writing_research_versions(task)),
            ("background_research", background_research_versions(task)),
        ):
            result.extend(
                ProductionIntermediateSummary(
                    id=f"{kind}:{task.id}:{snapshot.version}",
                    task_id=task.id,
                    kind=kind,
                    version=snapshot.version,
                    created_at=snapshot.created_at,
                )
                for snapshot in versions
                if snapshot.task_id == task.id
            )
        for kind, payload, created_at in (
            (
                "reader_observation",
                task.reader_observation_json,
                task.reader_observation_created_at or historical.get(task.id, ""),
            ),
            ("article_edit", task.editing_result_json, task.updated_at if task.status == "succeeded" else ""),
        ):
            if payload:
                result.append(
                    ProductionIntermediateSummary(
                        id=f"{kind}:{task.id}", task_id=task.id, kind=kind, created_at=created_at
                    )
                )
    return result
