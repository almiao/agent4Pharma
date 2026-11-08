"""内容窗口去重不越过来源身份，也不丢失非连续范围的位置。"""

from agent4pharma.modules.content.draft_contracts import EvidenceExcerpt, ResearchEvidence
from agent4pharma.modules.materials.document_reading import DocumentReference
from agent4pharma.workflows.evidence_delivery import EvidenceDelivery


def source(identity="e", text="abcdefghijklmnopqrstuvwxyz", document=None):
    return ResearchEvidence(
        id=identity, title="source", text=text, origin="primary_fulltext", document=document
    )


def excerpt(item, start, end):
    return EvidenceExcerpt(
        evidence_id=item.id, content_hash=item.content_hash, start=start, end=end, text=item.text[start:end]
    )


def test_disjoint_overlap_keeps_absolute_coordinates_and_only_new_text():
    state, item = EvidenceDelivery(), source()
    state.deliver(item, excerpt(item, 3, 8))
    state.deliver(item, excerpt(item, 14, 20))
    result = state.deliver(item, excerpt(item, 0, 24))
    assert [(r.start, r.end) for r in result.reused] == [(3, 8), (14, 20)]
    assert [(r.start, r.end) for r in result.segments] == [(0, 3), (8, 14), (20, 24)]
    assert result.text == ""
    for piece in result.segments:
        assert piece.text == item.text[piece.start : piece.end]
    assert state.deliver(item, excerpt(item, 4, 18)).already_read


def test_identical_text_from_unrelated_sources_is_not_interchangeable():
    state, first, second = EvidenceDelivery(), source("a"), source("b")
    state.deliver(first, excerpt(first, 0, 26))
    result = state.deliver(second, excerpt(second, 0, 26))
    assert result.text == second.text and not result.reused


def test_same_document_unique_text_maps_to_new_window_but_ambiguous_text_does_not():
    doc = DocumentReference(id="doc", sha256="a" * 64, pages=10)
    text = "Long exact source quotation. " * 4
    first = source("a", text, doc)
    second = source("b", "prefix" + text + "suffix", doc)
    state = EvidenceDelivery()
    state.deliver(first, excerpt(first, 0, len(text)))
    result = state.deliver(second, excerpt(second, 0, len(second.text)))
    assert [(r.start, r.end) for r in result.reused] == [(6, 6 + len(text))]
    ambiguous = source("c", text + "separator" + text, doc)
    assert not state.deliver(ambiguous, excerpt(ambiguous, 0, len(ambiguous.text))).reused


def test_changed_version_and_explicit_reload_always_return_requested_text():
    state, first = EvidenceDelivery(), source()
    state.deliver(first, excerpt(first, 0, 26))
    changed = source(text="abcdefghijklmnopqrstuvwxyz!")
    assert state.deliver(changed, excerpt(changed, 0, 26)).text == first.text
    assert state.deliver(first, excerpt(first, 0, 26), reread=True).text == first.text


def test_large_document_window_then_different_small_evidence_identity():
    doc = DocumentReference(id="doc", sha256="a" * 64, pages=10)
    text = "A unique experimental observation with an explicitly identified actor and measured result."
    large = source("large", "heading " + text + " trailing paragraph", doc)
    small = source("small", text, doc)
    state = EvidenceDelivery()
    state.deliver(large, excerpt(large, 0, len(large.text)))
    result = state.deliver(small, excerpt(small, 0, len(small.text)))
    assert result.already_read and not result.text
    assert result.reused[0].source_start == 8
