"""重复来源和可忽略复用分别标记，不将新版本或重新评估当作可忽略。"""

from agent4pharma.modules.discovery.run_duplicates import annotate_run_duplicates
from agent4pharma.modules.discovery.run_records import RunArticle


def article(identity, material="paper", kind="first_discovered", state="pending"):
    return RunArticle(
        id=identity, material_id=material, source_id=identity, title=identity, member_kind=kind, state=state
    )


def test_duplicates_require_explicit_material_identity_and_do_not_hide_updates():
    rows = [
        article("first"),
        article("other-source"),
        article("updated", kind="version_update"),
        article("reused", material="old", kind="seen_before", state="already_seen"),
        article("reevaluate", material="reevaluate", kind="policy_reevaluation"),
        article("reset", material="reset", kind="seen_before", state="awaiting_intelligence"),
    ]
    annotate_run_duplicates(rows)
    assert rows[0].duplicate_kind == ""
    assert rows[1].duplicate_kind == "same_material"
    assert rows[1].duplicate_of == "first"
    assert not rows[1].ignore_recommended
    assert rows[2].duplicate_kind == ""
    assert rows[3].ignore_recommended
    assert rows[3].duplicate_kind == "already_assessed"
    assert not rows[4].ignore_recommended
    assert not rows[5].ignore_recommended
