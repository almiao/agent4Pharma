"""本地渠道包的当前有效性校验；查询与实际导出使用同一规则。"""

import hashlib

from agent4pharma.modules.publishing.release_contracts import ChannelPackage
from agent4pharma.workflows.article_product_versions import bound_product
from agent4pharma.workflows.production_dependencies import ProductionUnit


def require_current_package(work: ProductionUnit, package: ChannelPackage) -> None:
    """无写副作用；任务书/选题/最新稿件或包内容变化时抛出 ValueError。"""
    if package.edit_pending:
        # edit_pending 是既有人工编辑版本标记；人工可直接负责定稿，不再要求模型复核。
        bound_product(work, package.id)
        packages = {p.id: p for p in work.releases.packages()}
        parent = packages.get(package.edit_parent_id)
        root = packages.get(package.edit_root_id)
        if (
            not parent
            or not root
            or parent.story_id != package.story_id
            or root.story_id != package.story_id
            or (parent.edit_root_id or parent.id) != root.id
        ):
            raise ValueError("人工修改版本缺少准确来源，不得定稿或导出")
    story = work.stories.get(package.story_id)
    draft = work.drafts.get(package.draft_id)
    if (
        story.status not in ({"active", "completed", "deferred"} if package.edit_pending else {"completed"})
        or story.brief_version != draft.brief_version
        # 历史模型复核可能只推进故事修订或要求返修；人工仍可确认同一准确图文。
        or (not package.edit_pending and story.revision != package.story_revision)
    ):
        raise ValueError("渠道包已经失效，不得导出")
    if draft.reader_policy and not package.edit_pending:
        from agent4pharma.workflows.production_dependencies import ProductionContext
        from agent4pharma.workflows.reader_readiness import require_reader_ready

        require_reader_ready(
            ProductionContext(
                story=story,
                draft=draft,
                reader_reviews=[r for r in work.reviews.reader_list() if r.draft_id == draft.id],
            )
        )
    latest = [d for d in work.drafts.list() if d.story_id == story.id][-1]
    if latest.id != draft.id or hashlib.sha256(package.body.encode()).hexdigest() != package.content_hash:
        raise ValueError("渠道包版本或内容已变")
    if package.presentation:
        presentation = package.presentation
        if (
            hashlib.sha256(presentation.wechat_html.encode()).hexdigest() != presentation.wechat_hash
            or hashlib.sha256(draft.submission.body.encode()).hexdigest() != presentation.source_body_hash
        ):
            raise ValueError("图文正文或公众号内容已变")
