"""人工审稿/交付清单的真实投影，与批准用例共用有效性检查。"""

from pydantic import BaseModel, Field

from agent4pharma.modules.content.draft_contracts import ResearchEvidence
from agent4pharma.modules.publishing.human_approval import HumanApproval, package_fingerprint
from agent4pharma.modules.publishing.release_contracts import ChannelPackage
from agent4pharma.modules.review.review_contracts import ReviewReport
from agent4pharma.workflows.human_package_approval import require_reviewable_package
from agent4pharma.workflows.production_dependencies import ProductionUnit


class ReviewablePackage(BaseModel):
    """列表只读摘要；完整正文通过明确选题范围按需读取。"""

    id: str
    story_id: str
    draft_id: str
    title: str
    fingerprint: str
    valid: bool
    blocker: str = ""
    approval: HumanApproval | None = None
    exported: bool = False
    package: ChannelPackage | None = None
    review: ReviewReport | None = None
    evidence: list[ResearchEvidence] = Field(default_factory=list)


class HumanReviewIndex(BaseModel):
    """真实渠道包与人工批准的当前关系。"""

    items: list[ReviewablePackage] = Field(default_factory=list)


def human_review_index(work: ProductionUnit, story_id: str = "") -> HumanReviewIndex:
    """不补造历史批准；检查失败原因就近呈现，正文只返回指定选题。"""
    drafts = {d.id: d for d in work.drafts.list()}
    approvals = work.releases.approvals()
    reports = {r.id: r for r in work.reviews.list()}
    exported = {r.package_id for r in work.releases.receipts() if r.status == "exported"}
    rows = []
    for package in work.releases.packages():
        if story_id and package.story_id != story_id:
            continue
        blocker = ""
        try:
            require_reviewable_package(work, package)
        except ValueError as exc:
            blocker = str(exc)
        fingerprint = package_fingerprint(package)
        rows.append(
            ReviewablePackage(
                id=package.id,
                story_id=package.story_id,
                draft_id=package.draft_id,
                title=drafts[package.draft_id].submission.title,
                fingerprint=fingerprint,
                valid=not blocker,
                blocker=blocker,
                approval=next(
                    (a for a in approvals if a.package_id == package.id and a.fingerprint == fingerprint),
                    None,
                ),
                exported=package.id in exported,
                package=package if story_id else None,
                review=reports.get(package.review_id) if story_id else None,
                evidence=drafts[package.draft_id].evidence if story_id else [],
            )
        )
    return HumanReviewIndex(items=rows)
