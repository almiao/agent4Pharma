"""绑定任务的证据工作集与技能读取记录；不拥有独立研究阶段或交接流程。"""

import hashlib

from pydantic import TypeAdapter

from agent4pharma.modules.content.draft_contracts import ResearchEvidence
from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.runtime.skill_resources import SkillResource
from agent4pharma.workflows.production_dependencies import ProductionContext, ProductionUnit

EVIDENCE_LIST = TypeAdapter(list[ResearchEvidence])


def citation_lookup_key(evidence_id: str, query: str, ignore_case: bool) -> str:
    """共用准确查询身份，供批次预检与单项持久化扣额使用。"""
    key = hashlib.sha256(f"{evidence_id}:{query}".encode()).hexdigest()
    return hashlib.sha256(f"ignore-case:{key}".encode()).hexdigest() if ignore_case else key


def require_skill_reads(task: ProductionTask) -> None:
    """只对冻结了阶段阅读要求的新任务生效；读取记录不等同于质量证明。"""
    missing = set(task.required_resources) - set(task.skill_reads)
    if missing:
        raise ValueError("提交前须读取阶段技能资源：" + ", ".join(sorted(missing)))


def read_skill(work: ProductionUnit, task: ProductionTask, resource: str) -> SkillResource:
    """按冻结白名单读取并记录宿主读取；调用者负责同事务租约校验。"""
    if task.newsroom_skills is None:
        raise ValueError("历史任务未绑定新闻技能包")
    result = task.newsroom_skills.read(resource)
    if resource in task.skill_reads:
        return result
    work.tasks.save(
        task.model_copy(update={"skill_reads": tuple(sorted(set(task.skill_reads) | {resource}))})
    )
    return result


def task_evidence(task: ProductionTask) -> list[ResearchEvidence]:
    """取得冻结输入和本任务实际补读的证据，拒绝同身份不同版本。"""
    context = ProductionContext.model_validate_json(task.input_json)
    sources = context.evidence + [e for c in context.candidates for e in c.evidence]
    if task.context_contract and context.draft:
        sources += context.draft.evidence
    if task.supplemental_evidence_json:
        sources += EVIDENCE_LIST.validate_json(task.supplemental_evidence_json)
    unique: dict[str, ResearchEvidence] = {}
    for source in sources:
        if source.id in unique and unique[source.id] != source:
            raise ValueError("同一证据身份存在不同版本")
        unique[source.id] = source
    return list(unique.values())


def save_task_evidence(work: ProductionUnit, task: ProductionTask, evidence: list[ResearchEvidence]) -> None:
    """预选题补读仅保存在绑定任务工作集；初始输入快照不改写。"""
    sources = {e.id: e for e in task_evidence(task)}
    for item in evidence:
        if item.id in sources and sources[item.id] != item:
            raise ValueError("证据身份冲突，请提供新的版本身份")
        sources[item.id] = item
    payload = EVIDENCE_LIST.dump_json(list(sources.values())).decode()
    work.tasks.save(task.model_copy(update={"supplemental_evidence_json": payload}))


def bound_task_evidence(work: ProductionUnit, task: ProductionTask) -> list[ResearchEvidence]:
    """新契约仅取冻结来源与本任务授权增量；旧任务维持原选题范围。"""
    if task.context_contract or not task.story_id:
        return task_evidence(task)
    return work.drafts.evidence(task.story_id)
