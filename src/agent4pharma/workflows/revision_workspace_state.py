"""返修候选的冻结身份加载与提交游标校验；工具和交稿事务共用。"""

from agent4pharma.modules.content.draft_contracts import DraftSubmission
from agent4pharma.modules.content.revision_workspace import RevisionWorkspace
from agent4pharma.modules.review.review_issue_scope import resolve_active_issue_set
from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.workflows.production_dependencies import ProductionContext
from agent4pharma.workflows.revision_context import is_patch_revision


def load_workspace(task: ProductionTask, context: ProductionContext) -> RevisionWorkspace:
    """验证契约、原稿和问题范围；拒绝跨稿件、跨评审挪用候选。"""
    if task.context_contract != "node-context-v2" or not is_patch_revision(task) or context.draft is None:
        raise ValueError("当前任务未开放返修候选工作区")
    issues = resolve_active_issue_set(
        context.reviews, context.reader_reviews, current_reports=task.revision_window_policy is not None
    )
    expected = (context.draft.id, context.draft.content_hash, issues.scope_hash)
    if task.revision_workspace_json:
        saved = RevisionWorkspace.model_validate_json(task.revision_workspace_json)
        if (saved.base_draft_id, saved.base_content_hash, saved.issue_scope_hash) != expected:
            raise ValueError("候选原稿或问题集合已变化，不能恢复")
        return saved
    return RevisionWorkspace(
        base_draft_id=expected[0], base_content_hash=expected[1], issue_scope_hash=expected[2]
    )


def committed_candidate(
    task: ProductionTask, context: ProductionContext, expected_version: int
) -> DraftSubmission:
    """正式提交时在交稿事务内再次核对候选游标，不能使用过期预检替代。"""
    workspace = load_workspace(task, context)
    if not workspace.version or workspace.version != expected_version:
        raise ValueError("候选版本已变化，请读取当前版本后提交")
    return workspace.current(context.draft)
