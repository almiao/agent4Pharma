"""已审核渠道包的本地图文制作：事务外取图，再校验原版本并幂等保存。"""

import hashlib
import time
from collections.abc import Callable
from contextlib import AbstractContextManager

from agent4pharma.modules.content.draft_contracts import DraftRevision, ResearchEvidence
from agent4pharma.modules.publishing.article_layout import (
    ArticleEmphasis,
    ArticlePresentation,
    FigureImage,
    FigureReader,
    FigureRecord,
    FigureSelection,
    LayoutRequest,
)
from agent4pharma.modules.publishing.article_renderer import (
    TEMPLATE_VERSION,
    article_paragraphs,
    emphasis_ranges,
    render_article,
    text_hash,
)
from agent4pharma.modules.publishing.paper_header import PaperHeader, PaperHeaderReader
from agent4pharma.modules.publishing.release_contracts import ChannelPackage
from agent4pharma.shared.clock import utcnow
from agent4pharma.workflows.production_dependencies import ProductionUnit
from agent4pharma.workflows.production_release_validation import require_current_package


def create_article_layout(
    unit: Callable[[], AbstractContextManager[ProductionUnit]],
    request: LayoutRequest,
    figure_reader: FigureReader | None,
    completion: tuple[str, str] | None = None,
    header_reader: PaperHeaderReader | None = None,
) -> ChannelPackage:
    """派生新包，不改变审核正文和旧包；相同输入复用产物，取图期间不占数据库事务。"""
    with unit() as work:
        source = next((p for p in work.releases.packages() if p.id == request.package_id), None)
        if source is None:
            raise KeyError("渠道包不存在")
        require_current_package(work, source)
        _validate_completion(work, completion, request)
        draft = work.drafts.get(source.draft_id)
        report = next((r for r in work.reviews.list() if r.id == source.review_id), None)
        if (
            report is None
            or report.submission.decision != "pass"
            or report.draft_hash != draft.content_hash
            or report.draft_id != draft.id
            or report.story_id != draft.story_id
            or report.brief_version != draft.brief_version
        ):
            raise ValueError("图文制作需要准确稿件的通过报告")
        identity = "layout-" + text_hash(source.id + request.model_dump_json() + TEMPLATE_VERSION)[:32]
        existing = next((p for p in work.releases.packages() if p.id == identity), None)
        if existing:
            require_current_package(work, existing)
            _finish_completion(work, completion, existing.id)
            return existing
        evidence = work.drafts.evidence(source.story_id)
    body, presentation = prepare_article_layout(
        draft,
        evidence,
        request.figures,
        figure_reader,
        request.emphasis,
        header_reader,
        excluded_source_ids=source.presentation.excluded_source_ids if source.presentation else None,
    )
    if completion:
        presentation = presentation.model_copy(
            update={"selection_task_id": completion[0], "model_calls": None, "total_tokens": None}
        )
    package = source.model_copy(
        update={
            "id": identity,
            "channel": "local_html",
            "body": body,
            "content_hash": text_hash(body),
            "presentation": presentation,
            "created_at": utcnow(),
            "release_note": "科研原图单栏阅读版；正文保持审核版本。公众号正文另存，未上传微信或对外发布。",
        }
    )
    with unit() as work:
        require_current_package(work, source)
        _validate_completion(work, completion, request)
        existing = next((p for p in work.releases.packages() if p.id == identity), None)
        if existing:
            require_current_package(work, existing)
            _finish_completion(work, completion, existing.id)
            return existing
        work.releases.save(package)
        _finish_completion(work, completion, package.id)
    return package


def _load_figures(
    selections: list[FigureSelection],
    evidence: list[ResearchEvidence],
    reader: FigureReader | None,
) -> list[tuple[FigureRecord, FigureImage]]:
    figures = []
    identities: set[tuple[str, int, int]] = set()
    # 先检查完整清单，避免错误身份浪费任何取图调用。
    for selection in selections:
        source = next((e for e in evidence if e.id == selection.evidence_id and e.document), None)
        if source is None or source.document is None:
            raise ValueError("配图必须来自当前选题绑定的论文")
        key = (source.document.sha256, selection.page, selection.block)
        if key in identities:
            raise ValueError("同一原图不能重复插入")
        identities.add(key)
    for selection in selections:
        source = next(e for e in evidence if e.id == selection.evidence_id)
        if reader is None or source.document is None:
            raise ValueError("论文原图读取工具未配置")
        image = reader(source.document, selection)
        record = FigureRecord(
            selection=selection,
            document_sha256=source.document.sha256,
            locator=image.locator,
            image_sha256=hashlib.sha256(image.png).hexdigest(),
            width=image.width,
            height=image.height,
            source_title=source.title,
            source_url=source.url,
        )
        figures.append((record, image))
    return figures


def _presentation(
    draft: DraftRevision,
    figures: list[tuple[FigureRecord, FigureImage]],
    wechat: str,
    seconds: float,
) -> ArticlePresentation:
    warnings = ["公众号图片当前为本地内嵌资源，需经微信编辑器或素材接口导入后实机验证。"]
    if figures:
        warnings.append("原图复用许可尚未核实；当前为本地排版预览，不能据此认定已获外部发布授权。")
    if any(record.width > 900 for record, _ in figures):
        warnings.append("包含宽幅原图，请检查手机上的坐标与图例是否可读。")
    return ArticlePresentation(
        template_version=TEMPLATE_VERSION,
        wechat_html=wechat,
        wechat_hash=text_hash(wechat),
        figures=[r for r, _ in figures],
        source_body_hash=text_hash(draft.submission.body),
        elapsed_seconds=round(seconds, 3),
        checks=["定稿正文逐段保留", "原图来自绑定PDF缓存", "图片内嵌无外部资源请求", "公众号正文内联样式"],
        warnings=warnings,
    )


def prepare_article_layout(
    draft: DraftRevision,
    evidence: list[ResearchEvidence],
    selections: list[FigureSelection],
    figure_reader: FigureReader | None,
    emphasis: ArticleEmphasis | None = None,
    header_reader: PaperHeaderReader | None = None,
    paper_header: PaperHeader | None = None,
    excluded_source_ids: list[str] | None = None,
) -> tuple[str, ArticlePresentation]:
    """事务外准备图文内容，供发行交接和定稿后的独立排版任务共用。"""
    started = time.monotonic()
    emphasis_ranges(draft.submission.body, emphasis)
    if any(f.after_paragraph > len(article_paragraphs(draft.submission.body)) for f in selections):
        raise ValueError("原图插入位置超出定稿段落范围")
    figures = _load_figures(selections, evidence, figure_reader)
    if paper_header is None and header_reader:
        documents = {e.document.sha256: e for e in draft.evidence if e.document}
        if len(documents) == 1:
            source = next(iter(documents.values()))
            paper_header = header_reader(source.document, source.title).model_copy(
                update={"evidence_id": source.id, "source_url": source.url}
            )
        else:
            paper_header = PaperHeader(status="skipped", reason="没有唯一绑定的主论文")
    cited = {claim.evidence_id for claim in draft.submission.claims}
    cited.update(figure.selection.evidence_id for figure, _ in figures)
    sources = list(
        dict.fromkeys(
            (e.title, e.url)
            for e in draft.evidence
            if e.id in cited
            and e.id not in (excluded_source_ids or [])
            and e.url.startswith(("http://", "https://"))
        )
    )
    body, wechat = render_article(
        draft.submission.title, draft.submission.body, figures, sources, emphasis, paper_header
    )
    presentation = _presentation(draft, figures, wechat, time.monotonic() - started)
    presentation = presentation.model_copy(
        update={
            "emphasis": emphasis,
            "paper_header": paper_header,
            "excluded_source_ids": excluded_source_ids or [],
        }
    )
    if paper_header:
        if paper_header.status == "ready":
            presentation.checks.append("题头来自绑定PDF首页，保留连续原始区域")
        else:
            presentation.warnings.append("已省略论文题头：" + paper_header.reason)
    return body, presentation


def _validate_completion(
    work: ProductionUnit, completion: tuple[str, str] | None, request: LayoutRequest
) -> None:
    if completion is None:
        return
    from agent4pharma.workflows.production_evidence import require_skill_reads
    from agent4pharma.workflows.production_figures import bound_figure_context, require_inspected_figures

    task, context = bound_figure_context(work, *completion)
    if context.layout_source_package != request.package_id:
        raise ValueError("排版任务的源包不符")
    require_skill_reads(task)
    require_inspected_figures(task, request.figures)


def _finish_completion(work: ProductionUnit, completion: tuple[str, str] | None, result: str) -> None:
    if completion:
        from agent4pharma.workflows.production_queue import finish_task

        finish_task(work, work.tasks.get(completion[0]), result)
