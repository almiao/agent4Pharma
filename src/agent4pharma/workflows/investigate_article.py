"""单篇文章的规则/Agent 调查流程，保留未验证和失败，不制造原始来源结论。"""

from agent4pharma.modules.maintenance.execution import InvestigationAgent
from agent4pharma.modules.maintenance.identifiers import normalized_url, reference_url
from agent4pharma.modules.maintenance.investigation_tools import InvestigationTools
from agent4pharma.modules.maintenance.models import InvestigationReport, StageResult
from agent4pharma.modules.maintenance.provenance_analysis import candidate_links


def investigate_article(
    tools: InvestigationTools, agent: InvestigationAgent | None = None
) -> InvestigationReport:
    """从输入起步有界追踪；模型失败或未提交合法结论时保留部分证据。"""
    job = tools.get_investigation()
    tools.repository.update_job(job.id, "running")
    tools.seed_manual_evidence()
    try:
        if url := reference_url(job.request.case.reference):
            tools.fetch_page(url)
        elif not job.request.case.text:
            tools.search_literature(job.request.case.title or job.request.case.reference[:500])
        if job.request.case.manual_original_url:
            tools.fetch_page(job.request.case.manual_original_url)
        if job.request.options.mode == "agent":
            if agent is None:
                raise ValueError("未配置模型调查运行器")
            agent.run(job.id)
            saved = tools.get_investigation().result
            report = saved if saved and saved.agent_finding else tools.build_report()
            if not report.agent_finding:
                report.notes.append("模型未保存通过证据校验的结构化结论，本次仅保留已取得证据")
                tools.repository.update_job(job.id, "partial", report)
                return report
            if not report.intelligence_results or any(
                t.status != "complete" for t in report.intelligence_results
            ):
                report.notes.append("来源调查已留档，情报复测未完成；没有生成正式主编候选")
                tools.repository.update_job(job.id, "partial", report)
                return report
        else:
            _follow_references(tools)
            report = tools.build_report()
            report.stages.append(
                StageResult(
                    name="Agent 语义调查",
                    status="not_requested",
                    detail="规则模式没有调用模型；引用链仍需人工 review",
                )
            )
        status = (
            "complete" if any(p.status in {"fetched", "manual_input"} for p in report.pages) else "partial"
        )
        tools.repository.update_job(job.id, status, report)
        return report
    except Exception as exc:
        report = tools.get_investigation().result or tools.build_report()
        # 模型 SDK 的 ValueError 也可能包含原始响应或凭证，不能直接持久化异常文本。
        error = type(exc).__name__
        report.notes.append("调查未完成，页面/人工证据已保留；错误：" + error)
        tools.repository.update_job(job.id, "partial" if report.pages else "failed", report, error)
        return report


def _follow_references(tools: InvestigationTools) -> None:
    job = tools.get_investigation()
    for _ in range(job.request.options.max_pages):
        pages = tools.evidence.list_evidence(job.id)
        attempted = {normalized_url(p.requested_url) for p in pages}
        network_pages = [p for p in pages if p.status != "manual_input"]
        if len(network_pages) >= job.request.options.max_pages:
            break
        next_link = next(
            (link for _, link in candidate_links(pages) if normalized_url(link.url) not in attempted), None
        )
        if next_link is None:
            break
        tools.fetch_page(next_link.url)
