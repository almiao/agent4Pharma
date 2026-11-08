from agent4pharma.modules.review.reader_review_contracts import ReaderReviewReport
from agent4pharma.modules.review.review_contracts import ReviewReport
from agent4pharma.modules.review.review_issue_scope import resolve_active_issue_set


def reader(identity, *, issues=True, closed=False):
    return ReaderReviewReport.model_validate(
        {
            "id": identity,
            "story_id": "story",
            "draft_id": "draft-" + identity,
            "draft_hash": identity,
            "brief_version": 1,
            "reviewer_id": "reviewer",
            "audience_hash": "audience",
            "rubric_hash": "rubric",
            "created_at": "2026-11-07T00:00:00+00:00",
            "observation": {
                "news_understood": "读者能够理解研究的主要发现。",
                "reason_to_read": "确认方法的实验价值。",
                "first_obstacle": "主体不清楚。",
            },
            "submission": {
                "decision": "changes_requested" if issues else "ready",
                "summary": "需要明确实验主体和测量结果。",
                "appeal_and_flow": "completed",
                "language_naturalness": "completed",
                "issues": [
                    {
                        "id": "RR-04",
                        "dimension": "appeal_and_flow",
                        "severity": "required",
                        "anchors": ["实验"],
                        "problem": "实验的主体与测量方法不清楚。",
                        "reader_impact": "会误解既往实验属于本研究。",
                        "resolution": "核对原文完整主语并修改归属。",
                    }
                ]
                if issues
                else [],
                "closed_issue_ids": ["RR-04"] if closed else [],
            },
        }
    )


def fact(identity, *, issue=True):
    return ReviewReport.model_validate(
        {
            "id": identity,
            "story_id": "story",
            "draft_id": "draft-" + identity,
            "draft_hash": identity,
            "brief_version": 1,
            "reviewer_id": "reviewer",
            "created_at": "2026-11-07T00:00:00+00:00",
            "submission": {
                "decision": "changes_requested" if issue else "pass",
                "summary": "独立核验当前稿件中的实验主体。",
                "issues": [
                    {
                        "id": "F-01",
                        "severity": "required",
                        "location": "paragraph",
                        "reason": "测量方法的主体归属与原文不符。",
                        "resolution": "按原文完整实验主体纠正归属。",
                    }
                ]
                if issue
                else [],
            },
        }
    )


def test_current_report_replaces_old_fact_and_reopened_issue_is_not_closed():
    reviews = [fact("f1"), fact("f2", issue=False)]
    readers = [reader("r1"), reader("r2", issues=False, closed=True), reader("r3")]
    old = resolve_active_issue_set(reviews, readers)
    assert old.required_ids == ["F-01", "RR-04"] and old.closed_reader_ids == ["RR-04"]
    current = resolve_active_issue_set(reviews, readers, current_reports=True)
    assert current.required_ids == ["RR-04"] and current.closed_reader_ids == []
    assert current.report_refs == ["f2", "r3"]
    assert current.origins[0].report_id == "r3" and current.origins[0].draft_hash == "r3"
    altered_history = [reader("irrelevant-history"), *readers[1:]]
    assert (
        resolve_active_issue_set(reviews, altered_history, current_reports=True).scope_hash
        == current.scope_hash
    )
    assert (
        resolve_active_issue_set(reviews, [reader("r4")], current_reports=True).scope_hash
        != current.scope_hash
    )
