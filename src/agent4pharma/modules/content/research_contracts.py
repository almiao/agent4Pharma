"""作者研究底稿中的证据事实；保留连续来源窗口和必要适用边界。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SerializerFunctionWrapHandler, model_serializer

from agent4pharma.modules.content.draft_contracts import EvidenceSpan


class ResearchFinding(BaseModel):
    """一项结果、数字、图或比较的解释及其连续原文依据。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["finding", "number", "figure", "comparison", "background"]
    statement: str = Field(min_length=10, max_length=2000)
    evidence_id: str
    source_span: EvidenceSpan
    limitation: str = Field(default="", max_length=2000)
    narrative_use: Literal["", "main_result", "explanation", "boundary", "context", "omit"] = ""
    selection_reason: str = Field(default="", max_length=600)
    source_relation: Literal["unknown", "original_research", "team_explanation", "external_commentary"] = (
        "unknown"
    )

    @model_serializer(mode="wrap")
    def serialize_finding(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        """省略未填写的编辑信息，旧底稿和冻结任务保持原序列化内容。"""
        result = handler(self)
        for key in ("narrative_use", "selection_reason"):
            if not result[key]:
                result.pop(key)
        if result["source_relation"] == "unknown":
            result.pop("source_relation")
        return result
