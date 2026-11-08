"""人工直接编辑准确图文文本，复用原图渲染与原子版本保存，不排队模型。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent4pharma.modules.content.article_editing import ManualArticleEdit
from agent4pharma.modules.content.draft_contracts import DraftSubmission, validate_draft
from agent4pharma.modules.publishing.article_renderer import article_paragraphs, text_hash
from agent4pharma.modules.publishing.release_contracts import ChannelPackage
from agent4pharma.shared.clock import utcnow
from agent4pharma.workflows.article_product_editing import render_edited_product, save_product_version
from agent4pharma.workflows.article_product_versions import bound_product
from agent4pharma.workflows.production_dependencies import ProductionUnit

if TYPE_CHECKING:
    from agent4pharma.workflows.production_workbench import ProductionWorkbench


def _require_idle(work: ProductionUnit, story_id: str) -> None:
    if any(t.story_id == story_id and t.status in {"queued", "running"} for t in work.tasks.list()):
        raise ValueError("任务执行中，请结束后保存手动修改")


def save_manual_article(app: ProductionWorkbench, request: ManualArticleEdit) -> ChannelPackage:
    """基于准确源包追加待人工定稿版本；重复相同请求幂等，过期或忙碌时拒绝保存。

    渲染在事务外执行，提交前重验当前版本、故事修订和任务状态。
    原图字节、证据与历史版本保留；人工改字不构造模型任务或继承批准。
    """
    # 未指定来源显示控制的旧请求保持原幂等指纹。
    identity = text_hash(
        request.model_dump_json(
            exclude={"excluded_source_ids"} if request.excluded_source_ids is None else set()
        )
    )[:32]
    package_id = "manual-package-" + identity
    with app.unit() as work:
        existing = next((p for p in work.releases.packages() if p.id == package_id), None)
        if existing:
            return existing
        source = bound_product(work, request.package_id)
        _require_idle(work, source.story_id)
        if source.draft_id != request.draft_id:
            raise ValueError("稿件与图文版本不匹配，请重新打开手动编辑")
        revision = work.stories.get(source.story_id).revision
        original = work.drafts.get(source.draft_id)
        evidence = work.drafts.evidence(source.story_id)
        records = source.presentation.figures if source.presentation else []
        excluded = source.presentation.excluded_source_ids if source.presentation else []
        if request.excluded_source_ids is not None:
            if source.presentation is None:
                raise ValueError("当前图文没有可编辑的排版信息")
            if not set(request.excluded_source_ids) <= {e.id for e in original.evidence}:
                raise ValueError("隐藏的来源不属于当前稿件证据")
            excluded = list(dict.fromkeys(request.excluded_source_ids))
        if len(request.figures) != len(records):
            raise ValueError("图片记录与当前图文不匹配")
        if not request.title.strip() or not request.body.strip():
            raise ValueError("标题和正文不能为空")
        selections = [
            record.selection.model_copy(update=figure.model_dump())
            for record, figure in zip(records, request.figures, strict=True)
        ]
        if any(s.after_paragraph > len(article_paragraphs(request.body)) for s in selections):
            raise ValueError("图片位置超出正文段落，请调整图片所在段落")
        claims = original.submission.claims
        if request.claim_texts is not None:
            if len(request.claim_texts) != len(claims):
                raise ValueError("证据关联记录与原稿不匹配")
            claims = [
                claim.model_copy(update={"claim": text})
                for claim, text in zip(claims, request.claim_texts, strict=True)
                if text.strip()
            ]
        if not claims or any(c.claim not in request.body for c in claims):
            raise ValueError("请确认关键表述对应的正文原句；至少保留一处真实证据关联")
        submission = DraftSubmission.model_validate(
            original.submission.model_dump()
            | {"title": request.title, "body": request.body, "claims": [c.model_dump() for c in claims]}
        )
        sources_unchanged = excluded == (
            source.presentation.excluded_source_ids if source.presentation else []
        )
        if (
            sources_unchanged
            and submission == original.submission
            and all(
                selection == record.selection for selection, record in zip(selections, records, strict=True)
            )
        ):
            raise ValueError("内容没有变化，无需保存")
        validate_draft(submission, evidence, False)
        draft = original.model_copy(
            update={
                "id": "draft-manual-" + identity,
                "number": original.number + 1,
                "submission": submission,
                "evidence": evidence,
                "content_hash": text_hash(submission.model_dump_json()),
                "created_at": utcnow(),
                "author_id": "human-manual:" + identity,
                "reader_policy": "reader-quality-v1"
                if app.definitions["reviewer"].newsroom_pipeline
                else original.reader_policy,
            }
        )
    rendering_source = source
    if source.presentation:
        rendering_source = source.model_copy(
            update={"presentation": source.presentation.model_copy(update={"excluded_source_ids": excluded})}
        )
    body, presentation = render_edited_product(app, rendering_source, draft, selections)
    with app.unit() as work:
        existing = next((p for p in work.releases.packages() if p.id == package_id), None)
        if existing:
            return existing
        bound_product(work, source.id)
        _require_idle(work, source.story_id)
        if work.stories.get(source.story_id).revision != revision:
            raise ValueError("图文上下文已变化，请重新打开手动编辑")
        return save_product_version(work, source, draft, body, presentation, package_id)
