"""图文编辑的准确版本校验，供读取、看图与保存共同调用。"""

from agent4pharma.modules.publishing.release_contracts import ChannelPackage
from agent4pharma.workflows.production_dependencies import ProductionUnit


def bound_product(work: ProductionUnit, package_id: str, *, current: bool = True) -> ChannelPackage:
    """按准确包绑定稿件，拒绝其他制作轮次或并发版本。"""
    source = next((p for p in work.releases.packages() if p.id == package_id), None)
    if source is None:
        raise ValueError("图文版本不存在")
    story = work.stories.get(source.story_id)
    draft = work.drafts.get(source.draft_id)
    if story.status == "cancelled" or draft.brief_version != story.brief_version:
        raise ValueError("图文任务书已失效")
    if current:
        latest = [d for d in work.drafts.list() if d.story_id == story.id][-1]
        descendants = [p for p in work.releases.packages() if p.edit_parent_id == source.id]
        if latest.id != source.draft_id or descendants:
            raise ValueError("图文版本已变化，请切换到最新版本后修改")
    return source
