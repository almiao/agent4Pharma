"""新上下文协议的确定性指令编译；不访问存储或替换历史冻结资源。"""

import re
from typing import Literal

from pydantic import BaseModel

from agent4pharma.runtime.skill_resources import SkillBundle
from agent4pharma.workflows.newsroom_skills import replace_skill_resources


class StageContextSpec(BaseModel):
    """冻结模型入场方式和上下文协议版本。"""

    contract: Literal["node-context-v1", "node-context-v2"] = "node-context-v1"
    prompt: str


def compile_context_spec(
    prompt: str,
    phase: str,
    *,
    revision: bool,
    contract: Literal["node-context-v1", "node-context-v2"] = "node-context-v1",
    bundled_reader: bool = False,
) -> StageContextSpec:
    """清除整包恢复指令，工具与权限由冻结版本选择，不靠提示词授权。"""
    if revision and contract == "node-context-v2":
        return StageContextSpec(
            contract=contract, prompt=prompt + "\n上下文协议node-context-v2：候选工作区返修。"
        )
    entry = "get_revision_context" if revision else "get_stage_context"
    if phase == "reading-review":
        entry = (
            "get_reader_review_context" if bundled_reader else "get_reader_draft，保存观察后get_reader_brief"
        )
    elif phase == "article-revision":
        entry = "get_article_edit_context"
    prompt = prompt.replace("通过get_production_context取得真实ID", "通过list_task_sources取得真实ID")
    prompt = prompt.replace(
        "先复用get_production_context返回的writing_research",
        "先按list_research_items/read_research_item复用研究条目",
    )
    prompt = prompt.replace(
        "需要底稿、背景或证据目录时才get_production_context",
        "需要底稿或背景时按研究目录分项读取，需要证据目录时用list_task_sources",
    )
    prompt = prompt.replace("get_production_context", "get_stage_context")
    prompt += f"\n上下文协议{contract}：入场使用{entry}。资料按节点职责提供，历史整包读取工具不开放。"
    if phase in {"topic-selection", "draft-writing", "fact-check"}:
        prompt += (
            "查来源ID或新搜索结果用list_task_sources，目录不带正文；"
            "get_task_checkpoint只返回版本游标、额度和保存点目录，不返回整稿或研究正文。"
            "需要已保存研究时先list_research_items，再按返回ref用read_research_item读一个条目；"
            "补读证据仍走read_evidence/read_document，引用保留准确hash和窗口。"
            "同一版本已在当前会话取得时复用；有缺失或需要核对时可明确重读。"
        )
        if phase in {"topic-selection", "draft-writing"}:
            prompt += "保存实质修改使用checkpoint的本任务写游标，不沿用上游报告版本。"
    elif phase == "layout-production":
        prompt += "来源目录用list_task_sources；不读取研究正文，不重新调查论文，按准确稿件选图。"
    return StageContextSpec(contract=contract, prompt=prompt)


def bind_context_resources(bundle: SkillBundle, phase: str) -> SkillBundle:
    """新契约适配已发布旧资源中的工具引用，并冻结派生 hash；不改发布库或旧任务。"""
    entry = "get_stage_context"
    routing = (
        f"新上下文契约：入场用{entry}，有当前稿件的返修用get_revision_context；"
        "get_task_checkpoint仅返回保存点身份和写游标，"
        "list_task_sources仅返回来源目录，read_evidence/read_document读取原文；"
        "底稿和背景通过list_research_items/read_research_item按项恢复；"
        "保存研究使用get_task_checkpoint的本任务write_expected_version，首次为0。"
    )
    if phase == "layout-production":
        routing = "用get_stage_context取得准确稿件，list_task_sources取得图源目录，不读取研究正文。"
    replacements = {}
    for resource in bundle.resources:
        if "get_production_context" not in resource.text:
            continue
        text = resource.text.replace(
            "get_production_context.draft.source_body_hash", "get_stage_context.draft.source_body_hash"
        )
        replacements[resource.name] = re.sub(r"[^。\n]*get_production_context[^。\n]*。?", routing, text)
    return replace_skill_resources(bundle, replacements) if replacements else bundle
