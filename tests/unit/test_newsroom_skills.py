import hashlib
import json

import pytest
from pydantic import ValidationError

from agent4pharma.runtime.skill_resources import SkillBundle, SkillResource
from agent4pharma.workflows.newsroom_skills import load_newsroom_skills, skill_loading_instructions


def test_roundtrip_freezes_resource_text_and_incremental_loading():
    bundle = load_newsroom_skills("writer")
    stored = SkillBundle.model_validate_json(bundle.model_dump_json())
    assert stored == bundle
    header = skill_loading_instructions(bundle)
    assert bundle.content_hash in header
    assert "# 找核与攻核" not in header
    name = "skills/draft-writing/core/core-finding.md"
    assert "Held" in stored.read(name).text and "Broken" in stored.read(name).text
    assert {s.name for s in bundle.skills} == {
        "paper-understanding",
        "context-research",
        "draft-writing",
        "evidence-check",
    }


@pytest.mark.parametrize("name", ["../LICENSE", "C:/secret", "/etc/passwd", "skills/./fact-check/SKILL.md"])
def test_read_is_exact_whitelist_not_filesystem(name):
    with pytest.raises(ValueError, match="不属于"):
        load_newsroom_skills("writer").read(name)


def test_profile_isolation_and_tampered_snapshot_rejected():
    editorial = load_newsroom_skills("editor")
    with pytest.raises(ValueError, match="不属于"):
        editorial.read("skills/draft-writing/SKILL.md")
    data = json.loads(editorial.model_dump_json())
    data["resources"][0]["text"] += "unrecorded change"
    with pytest.raises(ValidationError, match="hash"):
        SkillBundle.model_validate(data)
    with pytest.raises(ValueError, match="未知"):
        load_newsroom_skills("arbitrary")


def test_catalog_rejects_missing_entrypoint_and_duplicate_resources():
    bundle = load_newsroom_skills("editor")
    data = json.loads(bundle.model_dump_json())
    data["skills"][0]["entrypoint"] = "missing.md"
    with pytest.raises(ValidationError, match="缺失"):
        SkillBundle.model_validate(data)
    data = json.loads(bundle.model_dump_json())
    data["resources"].append(data["resources"][0])
    with pytest.raises(ValidationError, match="重复"):
        SkillBundle.model_validate(data)
    with pytest.raises(ValidationError, match="相对"):
        SkillResource(name="../bad.md", text="x", sha256=hashlib.sha256(b"x").hexdigest())
