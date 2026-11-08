"""发行任务的有界图文检索、看图与提交校验；不向模型开放全篇扫描。"""

import base64
import re
from collections.abc import Callable
from contextlib import AbstractContextManager

from mcp.types import CallToolResult, ImageContent, TextContent

from agent4pharma.modules.publishing.article_layout import FigureSelection
from agent4pharma.modules.publishing.figure_catalog import (
    FigureCatalogReader,
    FigureInspector,
    FigureSearchResult,
)
from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.workflows.production_dependencies import ProductionContext, ProductionUnit
from agent4pharma.workflows.production_release_validation import require_current_package
from agent4pharma.workflows.production_story_state import active_story
from agent4pharma.workflows.production_tasks import require_lease

Unit = Callable[[], AbstractContextManager[ProductionUnit]]


def bound_figure_context(
    work: ProductionUnit, task_id: str, token: str
) -> tuple[ProductionTask, ProductionContext]:
    """每次访问校验发行租约和当前版本；独立排版任务允许选题已完成。"""
    task = work.tasks.get(task_id)
    require_lease(
        task,
        token,
        "writer" if task.phase == "article-revision" and task.web_search_enabled else "publisher",
    )
    context = ProductionContext.model_validate_json(task.input_json)
    if task.phase == "article-revision" and task.web_search_enabled:
        from agent4pharma.workflows.article_product_versions import bound_product

        bound_product(work, context.article_edit.package_id)
    elif context.layout_source_package:
        source = next((p for p in work.releases.packages() if p.id == context.layout_source_package), None)
        if source is None:
            raise ValueError("原渠道包不存在")
        require_current_package(work, source)
    else:
        active_story(work, task)
    return task, context


def find_paper_figures(
    unit: Unit,
    reader: FigureCatalogReader | None,
    task_id: str,
    token: str,
    evidence_id: str,
    query: str,
    page: int = 0,
) -> FigureSearchResult:
    """一次返回至多3个相关图页；6次不同查询封顶，同查询不重复输出。"""
    if not 1 <= len(query.strip()) <= 120 or page < 0:
        raise ValueError("查询需1至120字符，页码不能为负")
    with unit() as work:
        task, context = bound_figure_context(work, task_id, token)
        source = next((e for e in context.evidence if e.id == evidence_id and e.document), None)
        if source is None or reader is None:
            raise ValueError("未配置绑定论文图目录")
        key = f"{source.document.sha256}:{page}:{query.strip().casefold()}"
        if key in task.figure_queries:
            return FigureSearchResult(
                evidence_id=evidence_id,
                queries_remaining=6 - len(task.figure_queries),
                images_remaining=6 - len(task.figure_inspections),
                already_returned=True,
            )
        if len(task.figure_queries) >= 6:
            raise ValueError("图目录查询额度已用完；复用已有结果，不遍历全文")
    catalog = reader(source.document)
    terms = re.findall(r"[\w]+", query.casefold())

    def score(item):
        text = (item.caption_context + " ".join(c.nearby_text for c in item.candidates)).casefold()
        return sum(min(text.count(term), 3) for term in terms)

    selected = (
        [p for p in catalog.pages if p.page == page]
        if page
        else sorted((p for p in catalog.pages if score(p)), key=lambda p: (-score(p), p.page))[:3]
    )
    reused = [p.page for p in selected if f"{source.document.sha256}:{p.page}" in task.figure_pages]
    selected = [p for p in selected if p.page not in reused]
    selected = [
        p.model_copy(
            update={
                "caption_context": _snippets(p.caption_context, terms),
                "candidates": [
                    c.model_copy(update={"nearby_text": c.nearby_text[:180]}) for c in p.candidates
                ],
            }
        )
        for p in selected
    ]
    with unit() as work:
        task, _ = bound_figure_context(work, task_id, token)
        if key not in task.figure_queries:
            if len(task.figure_queries) >= 6:
                raise ValueError("图目录查询额度已用完")
            task = task.model_copy(
                update={
                    "figure_queries": (*task.figure_queries, key),
                    "figure_pages": tuple(
                        dict.fromkeys(
                            (*task.figure_pages, *(f"{source.document.sha256}:{p.page}" for p in selected))
                        )
                    ),
                }
            )
            work.tasks.save(task)
    return FigureSearchResult(
        evidence_id=evidence_id,
        pages=selected,
        reused_pages=reused,
        queries_remaining=6 - len(task.figure_queries),
        images_remaining=6 - len(task.figure_inspections),
    )


def inspect_paper_figures(
    unit: Unit,
    inspector: FigureInspector | None,
    task_id: str,
    token: str,
    evidence_id: str,
    figure_ids: list[str],
) -> CallToolResult:
    """最多批量3图、每任务最多6图；pN为带子图ID的页内缩略图索引，pNbM为子图，已看过的图不重传。"""
    if not 1 <= len(figure_ids) <= 3 or len(set(figure_ids)) != len(figure_ids):
        raise ValueError("每次请选择1至3个不同图片ID")
    with unit() as work:
        task, context = bound_figure_context(work, task_id, token)
        source = next((e for e in context.evidence if e.id == evidence_id and e.document), None)
        if source is None or inspector is None:
            raise ValueError("未配置绑定论文看图工具")
        fresh = [f for f in figure_ids if f"{source.document.sha256}:{f}" not in task.figure_inspections]
        if len(task.figure_inspections) + len(fresh) > 6:
            raise ValueError("看图额度最多6张，禁止逐页轮询")
    images = [(identity, inspector(source.document, identity)) for identity in fresh]
    with unit() as work:
        task, _ = bound_figure_context(work, task_id, token)
        keys = tuple(
            dict.fromkeys((*task.figure_inspections, *(f"{source.document.sha256}:{f}" for f in fresh)))
        )
        if len(keys) > 6:
            raise ValueError("看图额度已用完")
        work.tasks.save(task.model_copy(update={"figure_inspections": keys}))
    content = []
    for identity, image in images:
        content.append(
            TextContent(
                type="text",
                text=(
                    f"{identity} | {image.width}x{image.height} | {image.locator}\n"
                    "请实际识别图内标签、坐标和实验分组；不可用图注替代视觉核对。"
                ),
            )
        )
        content.append(
            ImageContent(type="image", data=base64.b64encode(image.png).decode("ascii"), mimeType="image/png")
        )
    content.append(
        TextContent(
            type="text", text=f"剩余看图额度：{6 - len(keys)}。已看过的图片不重复发送，复用会话图像。"
        )
    )
    return CallToolResult(content=content)


def require_inspected_figures(task: ProductionTask, figures: list[FigureSelection]) -> None:
    """提交只能使用实际返回过的子图，页总览不充当可发布图片；视觉观察随包留档。"""
    for figure in figures:
        context = ProductionContext.model_validate_json(task.input_json)
        source = next((e for e in context.evidence if e.id == figure.evidence_id and e.document), None)
        if source is None:
            raise ValueError("图源不属于任务绑定论文")
        key = f"{source.document.sha256}:p{figure.page}b{figure.block}"
        if key not in task.figure_inspections or len(figure.visual_observation.strip()) < 15:
            raise ValueError("须先看准确子图，并记录图中标签/坐标/分组的具体观察")


def _snippets(text: str, terms: list[str]) -> str:
    windows = []
    for term in terms:
        at = text.casefold().find(term)
        if at >= 0:
            window = text[max(0, at - 100) : at + 300]
            if window not in windows:
                windows.append(window)
    return " … ".join(windows)[:1500] or text[:1000]
