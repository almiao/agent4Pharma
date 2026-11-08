"""本地摘要队列的有界补取；复用原生摘要，缺失时按 DOI 批查，不创建商业模型任务。"""

from agent4pharma.infrastructure.connectors.abstract_lookup import (
    AbstractLookup,
    is_placeholder,
    normalize_doi,
)
from agent4pharma.modules.discovery.intelligence_contracts import IntelligenceEvidence
from agent4pharma.modules.discovery.paper_selection import PaperSelectionItem


def read_selection_abstracts(items: list[PaperSelectionItem]) -> list[IntelligenceEvidence]:
    """一次最多16篇；没有摘要返回明确未知，技术失败留 error，来源记录不改写。"""
    if len(items) > 16:
        raise ValueError("摘要补取每批最多16篇")
    missing = [
        normalize_doi(i.request.evidence[0].doi)
        for i in items
        if not (
            i.request.evidence[0].abstract_status == "found"
            and not is_placeholder(i.request.evidence[0].source_abstract)
        )
    ]
    hits = {}
    if any(missing):
        lookup = AbstractLookup()
        try:
            hits = lookup.fetch(missing)
        finally:
            lookup.close()
    result = []
    for item in items:
        primary = item.request.evidence[0]
        native = primary.abstract_status == "found" and not is_placeholder(primary.source_abstract)
        hit = hits.get(normalize_doi(primary.doi))
        text = primary.source_abstract if native else hit.text if hit and hit.status == "found" else ""
        status = "found" if text else hit.status if hit else "none"
        result.append(
            IntelligenceEvidence(
                id=primary.id,
                title=primary.title,
                text=text,
                doi=primary.doi,
                url=primary.url,
                origin="abstract",
                abstract_status=status,
                abstract_source=primary.abstract_source if native else hit.source if hit else "",
                status="available" if text else "access_failed" if status == "error" else "unavailable",
            )
        )
    return result
