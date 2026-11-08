"""候选预检与正式交稿共用校验；调用者拥有事务和最终状态迁移。"""

from agent4pharma.modules.content.draft_contracts import DraftSubmission, bind_draft_quotes, validate_draft
from agent4pharma.modules.review.review_issue_scope import resolve_active_issue_set
from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.workflows.production_dependencies import ProductionContext, ProductionUnit
from agent4pharma.workflows.production_evidence import bound_task_evidence
from agent4pharma.workflows.writing_research import require_writing_research


def validate_submission(
    work: ProductionUnit, task: ProductionTask, context: ProductionContext, result: DraftSubmission
) -> DraftSubmission:
    """检查研究前置、准确引用和问题回应，不认证科学解释或修改质量。"""
    result = DraftSubmission.model_validate(result.model_dump())
    context.writing_research = require_writing_research(task)
    evidence = bound_task_evidence(work, task)
    result = bind_draft_quotes(result, evidence)
    validate_draft(result, evidence, False)
    issues = resolve_active_issue_set(
        context.reviews, context.reader_reviews, current_reports=task.revision_window_policy is not None
    )
    if set(issues.required_ids) - set(result.resolved_issue_ids):
        raise ValueError("修订须逐项回应全部必改/阻断问题")
    reader_issues = {i.id: i for report in context.reader_reviews[-1:] for i in report.submission.issues}
    responses = [response.issue_id for response in result.reader_responses]
    if len(set(responses)) != len(responses) or set(responses) - set(issues.known_reader_ids):
        raise ValueError("阅读问题回应须使用本轮准确问题ID且不能重复")
    if {i.id for i in reader_issues.values() if i.severity == "required"} - set(responses):
        raise ValueError("reader_responses须逐项说明阅读必改的处理或保留理由")
    return result
