"""按任务父子身份读取论文制作链；立项前也可见，不输出论文正文或冻结提示词。"""

from pydantic import BaseModel

from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.workflows.production_dependencies import ProductionContext


class ProductionPaperFlow(BaseModel):
    """论文制作入口及完整阶段身份；成功提交的任务才是保存点。"""

    root_task_id: str
    paper_titles: list[str]
    candidate_ids: list[str]
    task_ids: list[str]
    leaf_task_ids: list[str]
    story_ids: list[str]
    savepoint_task_ids: list[str]


def production_paper_flows(tasks: list[ProductionTask], story_ids: set[str]) -> list[ProductionPaperFlow]:
    """仅沿显式 parent_task_id 建链；历史无父任务的孤立任务不猜配论文。"""
    children: dict[str, list[str]] = {}
    for task in tasks:
        children.setdefault(task.parent_task_id, []).append(task.id)
    flows = []
    for root in tasks:
        if root.phase != "topic-selection" or root.parent_task_id:
            continue
        context = ProductionContext.model_validate_json(root.input_json)
        identities: set[str] = set()
        pending = [root.id]
        while pending:
            identity = pending.pop()
            if identity not in identities:
                identities.add(identity)
                pending.extend(children.get(identity, []))
        linked = [t for t in tasks if t.id in identities]
        flows.append(
            ProductionPaperFlow(
                root_task_id=root.id,
                paper_titles=list(
                    dict.fromkeys(e.title for c in context.candidates for e in c.evidence if e.title)
                ),
                candidate_ids=[c.id for c in context.candidates],
                task_ids=[t.id for t in linked],
                leaf_task_ids=[t.id for t in linked if not children.get(t.id)],
                story_ids=sorted(
                    {ref for t in linked for ref in (t.story_id, t.result_ref) if ref in story_ids}
                ),
                savepoint_task_ids=[t.id for t in linked if t.status == "succeeded" and t.result_ref],
            )
        )
    return flows
