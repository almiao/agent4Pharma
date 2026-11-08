"""从实际留档证据投影摘要与原文取得状态；访问错误不能当作业务排除。"""

from collections.abc import Sequence

from agent4pharma.modules.discovery.intelligence_contracts import IntelligenceEvidence, IntelligenceTask


def evidence_status(task: IntelligenceTask) -> tuple[str, str]:
    """返回摘要、全文状态；未留档则未知，不凭 URL 或推荐结论推定全文已取得。"""
    return evidence_states([*task.request.evidence, *task.supplemental_evidence])


def evidence_states(evidence: Sequence[IntelligenceEvidence]) -> tuple[str, str]:
    """按证据状态和正文存在性投影；列表查询可传不含全文的等价证据快照。"""
    abstract = "unknown"
    for state in ("error", "none", "found"):
        if any(e.abstract_status == state for e in evidence):
            abstract = state
    fulltext = "unknown"
    for state in ("fetching", "error", "access_failed", "restricted", "preview", "truncated"):
        if any(e.status == state or e.origin == "source_" + state for e in evidence):
            fulltext = state
    if any(e.origin == "primary_page" and e.status == "available" for e in evidence):
        fulltext = "unconfirmed"
    if any(
        e.origin == "primary_fulltext_excerpt" and e.status == "available" and e.text.strip()
        for e in evidence
    ):
        fulltext = "excerpt"
    if any(
        e.origin in {"source_fulltext", "primary_fulltext"} and e.status == "available" and e.text.strip()
        for e in evidence
    ):
        fulltext = "available"
    return abstract, fulltext
