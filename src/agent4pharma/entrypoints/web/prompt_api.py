"""节点提示词目录与草稿/发布协议；不执行模型或修改凭证。"""

from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from agent4pharma.composition import build_prompt_repository
from agent4pharma.modules.discovery.triage_contracts import TriagePolicy
from agent4pharma.modules.model_configuration.prompt_contracts import (
    PromptDraft,
    PromptKey,
    PromptPublication,
)
from agent4pharma.modules.model_configuration.screening_prompts import split_screening_prompt
from agent4pharma.workflows.newsroom_standards import EDITORIAL_STANDARDS
from agent4pharma.workflows.prompt_catalog import PROMPT_NODES


class PromptAPI:
    """复用 HTTP 同源令牌边界，发布对比身份由仓储在写锁内检查。"""

    def __init__(self, root: Path, model: str):
        self.repository, self.model = build_prompt_repository(root), model

    def get(self) -> JsonValue:
        """按业务顺序列出真实版本、用途、输入和不可编辑约束。"""
        items = []
        catalog = self.repository.catalog([node.key for node in PROMPT_NODES])
        related = {
            key: next(v.id for v in catalog[key] if v.status in {"published", "builtin"})
            for key in ("triage",)
        }
        for node in PROMPT_NODES:
            standard = next((s for s in EDITORIAL_STANDARDS if s.key == node.key), None)
            constraints = ["工具权限、预算、输出结构和版本校验由程序执行。"]
            if node.key == "triage":
                constraints.append("固定二分类选项：" + str(TriagePolicy().criteria))
            if node.key == "news_ranking":
                constraints.append("独立原生 logit 排序；机构、期刊优先级及前十容量由程序执行。")
            if node.key == "news_comparison":
                constraints.append("一次覆盖冻结前十；JSON字段、逐篇引用和最多五篇下载容量由程序校验。")
            if node.key in {"news_ranking", "news_comparison"}:
                constraints.append("新运行自动附加当前目标读者规范；指定运行续跑保留原提示词和规范。")
            versions = catalog[node.key]
            payloads = []
            for version in versions:
                parts = split_screening_prompt(version.instructions)
                payloads.append(
                    {
                        **version.model_dump(mode="json"),
                        "instructions": parts.stage,
                        "effective_instructions": version.text(),
                        "shared_scope_ref": parts.scope_ref,
                    }
                )
            items.append(
                {
                    "key": node.key,
                    "kind": "standard" if standard or node.key == "screening_scope" else "prompt",
                    "exposure": [{"stage": stage, "usage": usage} for stage, usage in standard.exposure]
                    if standard
                    else [],
                    "title": node.title,
                    "purpose": node.purpose,
                    "inputs": node.inputs,
                    "model": (
                        "不单独调用模型"
                        if standard or node.key == "screening_scope"
                        else "本地 Decider（型号由启动配置指定）"
                        if node.key in {"triage", "news_ranking"}
                        else self.model
                    ),
                    "current_id": next(v.id for v in versions if v.status in {"published", "builtin"}),
                    "constraints": constraints,
                    "versions": payloads,
                    "expected_related": related if node.key == "screening_scope" else {},
                }
            )
        return {"items": items}

    def post(self, route: str, payload: JsonValue) -> JsonValue:
        """保存新草稿或发布具体草稿；任何操作都不调用模型。"""
        parts = route.strip("/").split("/")
        if len(parts) not in {3, 4} or parts[:2] != ["api", "prompts"]:
            raise KeyError("提示词接口不存在")
        key = TypeAdapter(PromptKey).validate_python(parts[2])
        if len(parts) == 3:
            result = self.repository.draft(key, PromptDraft.model_validate(payload))
        elif parts[3] == "publish":
            result = self.repository.publish(key, PromptPublication.model_validate(payload))
        else:
            raise KeyError("提示词操作不存在")
        return result.model_dump(mode="json")
