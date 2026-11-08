import pytest

from agent4pharma.modules.editorial.commission_contracts import CommissionBrief
from agent4pharma.modules.review.news_acceptance import validate_news_acceptance
from agent4pharma.modules.review.review_contracts import ReviewIssue, ReviewSubmission, validate_review


def test_model_submission_schemas_have_only_pass_or_revision_but_history_stays_readable():
    from agent4pharma.modules.review.reader_review_contracts import ReaderReviewResult, ReaderReviewSubmission
    from agent4pharma.modules.review.review_contracts import ReviewResult

    assert ReviewResult.model_json_schema()["properties"]["decision"]["enum"] == [
        "pass",
        "changes_requested",
    ]
    assert ReaderReviewResult.model_json_schema()["properties"]["decision"]["enum"] == [
        "ready",
        "changes_requested",
    ]
    assert "blocked" in ReviewSubmission.model_json_schema()["properties"]["decision"]["enum"]
    assert "rebrief_needed" in ReaderReviewSubmission.model_json_schema()["properties"]["decision"]["enum"]


def issue(**updates):
    values = dict(
        id="wrong-number",
        severity="required",
        category="factual_error",
        location="正文",
        reason="当前稿件数字与原文不一致。",
        resolution="按原文将错误的比例修正为10%。",
        draft_quote="活动增加了20%。",
        evidence_id="paper",
        source_quote="Activity increased by 10%.",
    )
    return ReviewIssue(**(values | updates))


def check(row, requirements=()):
    result = ReviewSubmission(
        decision="changes_requested", summary="核验当前准确版本，发现待修正问题。", issues=[row]
    )
    validate_news_acceptance(result, "活动增加了20%。", {"paper": "Activity increased by 10%."}, requirements)


def test_factual_error_needs_actual_draft_and_bound_source():
    check(issue())
    with pytest.raises(ValueError, match="问题句"):
        check(issue(draft_quote="首次增加了20%。"))
    with pytest.raises(ValueError, match="纠正依据"):
        check(issue(evidence_id="another-paper"))
    with pytest.raises(ValueError, match="纠正依据"):
        check(issue(source_quote="Activity increased by 30%."))


def test_style_preferences_are_excluded_from_new_reviews():
    with pytest.raises(ValueError, match="措辞与过渡"):
        check(issue(category="style"))
    result = ReviewSubmission(
        decision="pass",
        summary="核心事实准确，措辞改善仅作建议。",
        issues=[issue(category="style", severity="suggestion")],
    )
    with pytest.raises(ValueError, match="只收录必须返修"):
        validate_review(result, "author", "editor")
    validate_review(result.model_copy(update={"issues": []}), "author", "editor")


def test_requirement_gap_cannot_invent_acceptance_criteria():
    check(issue(category="requirement_gap", requirement="说明核心发现"), ["说明核心发现"])
    with pytest.raises(ValueError, match="任务书"):
        check(issue(category="requirement_gap", requirement="再增加两个案例"), ["说明核心发现"])


def test_commission_brief_accepts_angle_only_and_rejects_removed_fields():
    assert CommissionBrief(angle="发现及其读者意义").boundary == ""
    with pytest.raises(ValueError, match="angle"):
        CommissionBrief(angle="   ")
    with pytest.raises(ValueError, match="must_answer"):
        CommissionBrief.model_validate({"angle": "解释发现", "must_answer": ["发生什么"]})
