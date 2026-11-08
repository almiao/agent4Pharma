"""人工定点改稿的图文版本：原子保存、恢复和核验交接，不重跑作者写作。"""

from __future__ import annotations

import base64
from collections import defaultdict
from html.parser import HTMLParser
from typing import TYPE_CHECKING

from pydantic import BaseModel

from agent4pharma.modules.content.article_editing import ArticleEditResult, EditOption, validate_ranges
from agent4pharma.modules.content.draft_contracts import (
    DraftRevision,
    DraftSubmission,
    bind_draft_quotes,
    validate_draft,
)
from agent4pharma.modules.publishing.article_layout import (
    ArticleEmphasis,
    ArticlePresentation,
    FigureImage,
    FigureSelection,
)
from agent4pharma.modules.publishing.article_renderer import (
    article_paragraphs,
    text_hash,
)
from agent4pharma.modules.publishing.release_contracts import ChannelPackage
from agent4pharma.shared.clock import utcnow
from agent4pharma.workflows.article_product_versions import bound_product
from agent4pharma.workflows.production_dependencies import ProductionContext, ProductionUnit
from agent4pharma.workflows.production_queue import finish_task
from agent4pharma.workflows.production_tasks import require_lease
from agent4pharma.workflows.publication_layout import prepare_article_layout

if TYPE_CHECKING:
    from agent4pharma.workflows.production_workbench import ProductionWorkbench


class ProductEditAction(BaseModel):
    """操作准确图文版本；恢复目标必须属于同一编辑链。"""

    package_id: str
    restore_package_id: str = ""


def product_targets(package: ChannelPackage, draft: DraftRevision) -> dict[str, str]:
    """返回可选文字对象；图注身份由准确包内序号界定。"""
    targets = {"body": draft.submission.body, "title": draft.submission.title}
    if package.presentation:
        targets.update(
            {f"caption:{i}": f.selection.caption for i, f in enumerate(package.presentation.figures)}
        )
    return targets


def edit_submission(
    package: ChannelPackage, draft: DraftRevision, option: EditOption
) -> tuple[DraftSubmission, list[FigureSelection]]:
    """校验目标与定点补丁，保留所有未涉及文字、图源和图序。"""
    if not option.changes and not option.figures:
        raise ValueError("变更集不能为空；无需修改时返回空options")
    targets = product_targets(package, draft)
    grouped = defaultdict(list)
    for change in option.changes:
        if change.target not in targets:
            raise ValueError("修改对象不属于当前图文")
        grouped[change.target].append(change)
    body_changes = grouped.get("body", [])
    if body_changes and sum(c.end - c.start for c in body_changes) >= len(targets["body"]) * 0.8:
        raise ValueError("此处只支持定点修改，整篇重写请使用重做流程")
    for target, changes in grouped.items():
        validate_ranges(targets[target], changes)
        for change in sorted(changes, key=lambda c: c.start, reverse=True):
            targets[target] = (
                targets[target][: change.start] + change.replacement + targets[target][change.end :]
            )
    selections = [f.selection for f in package.presentation.figures] if package.presentation else []
    old_lines, new_lines = article_paragraphs(draft.submission.body), article_paragraphs(targets["body"])
    # 编辑未涉及的段落使用唯一原文重新定位；有歧义时要求模型明确提交图片位置。
    moves = {f.index: f for f in option.figures}
    if len(moves) != len(option.figures) or any(i >= len(selections) for i in moves):
        raise ValueError("图片修改重复或越界")
    output = []
    for i, selection in enumerate(selections):
        change = moves.get(i)
        if change and change.action == "remove":
            continue
        position = selection.after_paragraph
        if len(old_lines) != len(new_lines):
            anchor = old_lines[position - 1]
            if new_lines.count(anchor) == 1:
                position = new_lines.index(anchor) + 1
            elif not change or not change.after_paragraph:
                raise ValueError("段落变化影响图片位置，请为该图片明确after_paragraph")
        selection = selection.model_copy(
            update={"caption": targets[f"caption:{i}"], "after_paragraph": position}
        )
        if change:
            if change.action == "replace":
                selection = FigureSelection(
                    evidence_id=change.evidence_id,
                    page=change.page,
                    block=change.block,
                    label=change.label,
                    caption=change.caption,
                    visual_observation=change.visual_observation,
                    after_paragraph=change.after_paragraph or position,
                )
            elif change.after_paragraph:
                selection = selection.model_copy(update={"after_paragraph": change.after_paragraph})
            else:
                raise ValueError("移动图片须指定段落位置")
        output.append(selection)
    submission = DraftSubmission.model_validate(
        draft.submission.model_dump()
        | {
            "title": targets["title"],
            "body": targets["body"],
            "claims": option.claims or draft.submission.claims,
        }
    )
    return submission, output


class EmbeddedImages(HTMLParser):
    """提取宿主已有成品中的内嵌PNG，不请求网络或读取任意路径。"""

    def __init__(self, body: str) -> None:
        super().__init__()
        self.images: list[bytes] = []
        self.feed(body)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """只接收宿主PNG data URI。"""
        if tag == "img" and dict(attrs).get("data-paper-header") != "true":
            src = dict(attrs).get("src") or ""
            if not src.startswith("data:image/png;base64,"):
                raise ValueError("图文包含不可复用的图片来源")
            self.images.append(base64.b64decode(src.split(",", 1)[1], validate=True))


def render_edited_product(
    app: ProductionWorkbench, source: ChannelPackage, draft: DraftRevision, selections: list[FigureSelection]
) -> tuple[str, ArticlePresentation]:
    """事务外渲染；现有图直接复用内嵌字节，仅替换图读取绑定缓存。"""
    records = source.presentation.figures if source.presentation else []
    images = EmbeddedImages(source.body).images
    if len(records) != len(images):
        raise ValueError("原图记录与成品不一致，无法安全修改")
    cached = {}
    for record, png in zip(records, images, strict=True):
        if text_hash_bytes(png) != record.image_sha256:
            raise ValueError("成品图片指纹不一致")
        cached[(record.document_sha256, record.selection.page, record.selection.block)] = FigureImage(
            png, record.width, record.height, record.locator
        )

    def reader(document, selection):
        image = cached.get((document.sha256, selection.page, selection.block))
        if image:
            return image
        if app.figure_reader is None:
            raise ValueError("绑定论文图片缓存不可用")
        return app.figure_reader(document, selection)

    emphasis = None
    if source.presentation and source.presentation.emphasis:
        old = source.presentation.emphasis.items
        lines = article_paragraphs(draft.submission.body)
        kept = []
        for item in old:
            matches = [
                i + 1
                for i, line in enumerate(lines)
                if not line.startswith("## ") and line.count(item.quote) == 1
            ]
            if len(matches) == 1:
                kept.append(item.model_copy(update={"paragraph": matches[0]}))
        if kept:
            emphasis = ArticleEmphasis(source_body_hash=text_hash(draft.submission.body), items=kept)
    return prepare_article_layout(
        draft,
        draft.evidence,
        selections,
        reader,
        emphasis,
        paper_header=source.presentation.paper_header if source.presentation else None,
        excluded_source_ids=source.presentation.excluded_source_ids if source.presentation else None,
    )


def text_hash_bytes(value: bytes) -> str:
    """校验内嵌原图字节身份。"""
    import hashlib

    return hashlib.sha256(value).hexdigest()


def save_product_version(
    work: ProductionUnit,
    source: ChannelPackage,
    draft: DraftRevision,
    body: str,
    presentation: ArticlePresentation,
    identity: str,
) -> ChannelPackage:
    """同事务追加稿件和图文包，使旧批准立即失效；不排队模型。"""
    story = work.stories.get(source.story_id)
    updated = story.model_copy(
        update={"status": "active", "revision": story.revision + 1, "updated_at": utcnow()}
    )
    package = source.model_copy(
        update={
            "id": identity,
            "draft_id": draft.id,
            "story_revision": updated.revision,
            "review_id": "",
            "channel": "local_html",
            "body": body,
            "content_hash": text_hash(body),
            "presentation": presentation,
            "created_at": utcnow(),
            "edit_root_id": source.edit_root_id or source.id,
            "edit_parent_id": source.id,
            "edit_pending": True,
            "release_note": "人工修改；待人工审阅准确版本并确认定稿。",
        }
    )
    work.drafts.save(draft)
    work.stories.save(updated, story.revision)
    work.releases.save(package)
    return package


def apply_product_edit(app: ProductionWorkbench, task_id: str, token: str, result: ArticleEditResult) -> str:
    """模型提交一次变更集，先准备图文，再以租约和准确源版本原子保存。"""
    with app.unit() as work:
        task = work.tasks.get(task_id)
        require_lease(task, token, "writer")
        context = ProductionContext.model_validate_json(task.input_json)
        source = bound_product(work, context.article_edit.package_id)
        if work.stories.get(source.story_id).revision != task.expected_revision:
            raise ValueError("图文上下文已变化，请重新提交修改意见")
        if source.draft_id != context.article_edit.draft_id:
            raise ValueError("稿件与图文版本不匹配")
        if len(result.options) != 1:
            raise ValueError("直接修改需提交一个变更集；无法修改时返回空options并说明")
        original = work.drafts.get(source.draft_id)
        submission, selections = edit_submission(source, original, result.options[0])
        evidence = work.drafts.evidence(source.story_id)
        submission = bind_draft_quotes(submission, evidence)
        validate_draft(submission, evidence, False)
        from agent4pharma.workflows.production_figures import require_inspected_figures

        old_keys = (
            {
                (f.selection.evidence_id, f.selection.page, f.selection.block)
                for f in source.presentation.figures
            }
            if source.presentation
            else set()
        )
        require_inspected_figures(
            task, [s for s in selections if (s.evidence_id, s.page, s.block) not in old_keys]
        )
        draft = original.model_copy(
            update={
                "id": "draft-edit-" + task.id,
                "number": original.number + 1,
                "submission": submission,
                "evidence": evidence,
                "content_hash": text_hash(submission.model_dump_json()),
                "created_at": utcnow(),
                "author_id": "human-directed:" + task.id,
                "reader_policy": "reader-quality-v1" if app.definitions["reviewer"].newsroom_pipeline else "",
            }
        )
    body, presentation = render_edited_product(app, source, draft, selections)
    with app.unit() as work:
        task = work.tasks.get(task_id)
        require_lease(task, token, "writer")
        bound_product(work, source.id)
        if work.stories.get(source.story_id).revision != task.expected_revision:
            raise ValueError("图文上下文已变化，请重新提交修改意见")
        for item in evidence:
            work.drafts.add_evidence(source.story_id, item)
        package = save_product_version(work, source, draft, body, presentation, "edit-package-" + task.id)
        finish_task(
            work, task.model_copy(update={"editing_result_json": result.model_dump_json()}), package.id
        )
        return package.id


def restore_product(app: ProductionWorkbench, request: ProductEditAction) -> ChannelPackage:
    """恢复完整图文为新版本，历史包、稿件和旧批准保持不变。"""
    with app.unit() as work:
        source = bound_product(work, request.package_id)
        target = bound_product(work, request.restore_package_id, current=False)
        root = source.edit_root_id or source.id
        if (target.edit_root_id or target.id) != root or target.story_id != source.story_id:
            raise ValueError("恢复目标不属于当前图文编辑历史")
        if any(
            t.story_id == source.story_id and t.status in {"queued", "running"} for t in work.tasks.list()
        ):
            raise ValueError("任务执行中，请结束后恢复")
        original = work.drafts.get(source.draft_id)
        previous = work.drafts.get(target.draft_id)
        identity = text_hash(source.id + ":restore:" + target.id)[:32]
        draft = previous.model_copy(
            update={
                "id": "draft-restore-" + identity,
                "number": original.number + 1,
                "created_at": utcnow(),
                "author_id": "human-restored:" + target.id,
                "evidence": work.drafts.evidence(source.story_id),
                "reader_policy": "reader-quality-v1"
                if app.definitions["reviewer"].newsroom_pipeline
                else original.reader_policy,
            }
        )
    selections = [f.selection for f in target.presentation.figures] if target.presentation else []
    body, presentation = render_edited_product(app, target, draft, selections)
    with app.unit() as work:
        bound_product(work, source.id)
        if any(
            t.story_id == source.story_id and t.status in {"queued", "running"} for t in work.tasks.list()
        ):
            raise ValueError("任务执行中，请结束后恢复")
        return save_product_version(work, source, draft, body, presentation, "edit-restore-" + identity)
