"""版本化研究文档的条目目录与单项投影；目录不包含正文或原文引句。"""

import hashlib

from pydantic import BaseModel, JsonValue

from agent4pharma.runtime.context_records import ContextPayload


class ResearchItem(BaseModel):
    """准确快照内一个字段或列表成员；ref 包含内容版本，不能指向最新值。"""

    ref: str
    origin_task_id: str
    version: int
    section: str
    label: str
    characters: int


class ResearchItemRead(ContextPayload):
    """一个准确版本的研究条目，非原文证据替代品。"""

    item: ResearchItem
    value: JsonValue


def research_items(snapshot: BaseModel, kind: str) -> list[ResearchItemRead]:
    """将嵌套骨架按段、研究按事实/问题拆开；每项保留版本及原字段位置。"""
    payload = snapshot.model_dump(mode="json")
    digest = hashlib.sha256(snapshot.model_dump_json().encode()).hexdigest()
    result = []

    def add(path: str, value: JsonValue) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                add(f"{path}/{index}", item)
            return
        if path == "outline" and isinstance(value, dict):
            for key, item in value.items():
                add(f"outline/{key}", item)
            return
        if value is None or value == "":
            return
        label = path
        if isinstance(value, dict):
            label = str(
                value.get("question")
                or value.get("statement")
                or value.get("claim")
                or value.get("purpose")
                or path
            )[:160]
        result.append(
            ResearchItemRead(
                item=ResearchItem(
                    ref=f"{kind}:{digest}:{path}",
                    origin_task_id=payload["task_id"],
                    version=payload["version"],
                    section=path,
                    label=label,
                    characters=len(str(value)),
                ),
                value=value,
            )
        )

    for key, value in payload["research"].items():
        add(key, value)
    return result
