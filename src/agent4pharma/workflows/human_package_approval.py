"""人工定稿用例：准确版本与执行状态校验；人工编辑版无需再经模型核验。"""

from agent4pharma.modules.publishing.human_approval import (
    ApprovePackage,
    HumanApproval,
    package_fingerprint,
)
from agent4pharma.modules.publishing.release_contracts import ChannelPackage
from agent4pharma.shared.clock import utcnow
from agent4pharma.workflows.production_dependencies import ProductionUnit
from agent4pharma.workflows.production_release_validation import require_current_package


def require_reviewable_package(work: ProductionUnit, package: ChannelPackage) -> None:
    """自动制作包验证审核报告；人工编辑包由人工负责定稿，两者均拒绝在途任务。"""
    require_current_package(work, package)
    draft = work.drafts.get(package.draft_id)
    reports = [r for r in work.reviews.list() if r.draft_id == draft.id]
    report = reports[-1] if reports else None
    if not package.edit_pending and (
        report is None
        or report.id != package.review_id
        or report.submission.decision != "pass"
        or report.draft_hash != draft.content_hash
        or report.brief_version != draft.brief_version
    ):
        raise ValueError("当前稿件没有准确版本的最新核验通过记录")
    if any(t.story_id == package.story_id and t.status in {"queued", "running"} for t in work.tasks.list()):
        raise ValueError("此选题仍有待执行或运行中的任务，请完成后重新审稿")


def approve_package(work: ProductionUnit, request: ApprovePackage) -> HumanApproval:
    """调用者拥有工作单元；只保存人工确认，不调用模型或发送内容。"""
    package = next((p for p in work.releases.packages() if p.id == request.package_id), None)
    if package is None:
        raise KeyError("渠道包不存在")
    require_reviewable_package(work, package)
    fingerprint = package_fingerprint(package)
    if fingerprint != request.expected_fingerprint:
        raise ValueError("待审内容已变化，请重新读取完整发布包")
    existing = next(
        (a for a in work.releases.approvals() if a.package_id == package.id and a.fingerprint == fingerprint),
        None,
    )
    if existing:
        return existing
    approval = HumanApproval(
        id="approval-" + fingerprint,
        package_id=package.id,
        story_id=package.story_id,
        fingerprint=fingerprint,
        actor="local-operator",
        reason=request.reason,
        created_at=utcnow(),
    )
    work.releases.approve(approval)
    return approval
