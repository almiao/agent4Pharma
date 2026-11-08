"""局部稿件合成的版本、定位和证据继承边界。"""

import pytest

from agent4pharma.modules.content.draft_contracts import ClaimEvidence, DraftRevision, DraftSubmission
from agent4pharma.modules.content.draft_revision import (
    DraftRevisionSubmission,
    RevisionChange,
    apply_draft_revision,
)


def base():
    return DraftRevision(
        id="draft",
        story_id="story",
        brief_version=1,
        number=1,
        author_id="writer",
        content_hash="a" * 64,
        created_at="2026-11-05T00:00:00+00:00",
        evidence=[],
        submission=DraftSubmission(
            title="Title",
            body="Old fact. Other fact. " + "Background context. " * 5,
            summary="Summary",
            coverage="Bounded evidence report.",
            claims=[
                ClaimEvidence(claim="Old fact.", evidence_id="e", quote="Old source."),
                ClaimEvidence(claim="Other fact.", evidence_id="e", quote="Other source."),
            ],
        ),
    )


def patch(**kwargs):
    return DraftRevisionSubmission(
        draft_id="draft", content_hash="a" * 64, coverage="A targeted revision.", **kwargs
    )


def test_patch_removes_changed_claim_keeps_original_evidence_and_adds_replacement():
    old = base()
    claim = ClaimEvidence(claim="New fact.", evidence_id="new", quote="New source.")
    result = apply_draft_revision(
        old,
        patch(changes=[RevisionChange(original="Old fact.", replacement="New fact.")], added_claims=[claim]),
    )
    assert result.body == old.submission.body.replace("Old fact.", "New fact.")
    assert result.claims == [old.submission.claims[1], claim]
    assert old.submission.claims[0].claim == "Old fact."
    assert result.summary == old.submission.summary


@pytest.mark.parametrize(
    "changes",
    [
        [("Old fact.", "New"), ("Old fact. Other fact.", "New")],
        [("Background context.", "New")],
        [("missing", "New")],
    ],
)
def test_patch_rejects_overlap_ambiguity_and_missing_text(changes):
    with pytest.raises(ValueError):
        apply_draft_revision(
            base(), patch(changes=[RevisionChange(original=a, replacement=b) for a, b in changes])
        )


@pytest.mark.parametrize("indices", [[-1], [2], [0, 0]])
def test_patch_rejects_invalid_claim_removal(indices):
    with pytest.raises(ValueError):
        apply_draft_revision(base(), patch(removed_claim_indices=indices))


def test_patch_reports_added_claim_index_instead_of_merged_claim_index():
    claim = ClaimEvidence(
        claim="A paraphrase absent from the article", evidence_id="e", quote="Other source."
    )
    with pytest.raises(ValueError, match=r"added_claims\[0\]"):
        apply_draft_revision(base(), patch(added_claims=[claim]))


def test_candidate_can_save_missing_claims_but_formal_submission_cannot():
    from agent4pharma.modules.content.revision_workspace import CandidateEdit, edit_candidate

    original = base()
    current = edit_candidate(
        original,
        original.submission,
        CandidateEdit(
            changes=[RevisionChange(original="Old fact. Other fact.", replacement="New attributed result.")],
            reason="先保存局部修改，下一步补准确引用。",
        ),
    )
    assert current.claims == []
    assert "New attributed result." in current.body
    with pytest.raises(ValueError, match="claims"):
        DraftSubmission.model_validate(current.model_dump())
