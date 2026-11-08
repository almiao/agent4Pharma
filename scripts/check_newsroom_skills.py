"""Validate shipped newsroom skills, internal links, provenance and progressive resource coverage offline."""

import json
import re
from pathlib import Path

from agent4pharma.workflows.newsroom_skills import load_newsroom_skills


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "src/agent4pharma/agents/newsroom"
    bundle = load_newsroom_skills("newsroom")
    frozen = {r.name for r in bundle.resources}
    for entry in bundle.skills:
        text = bundle.read(entry.entrypoint).text
        assert text.startswith("---\n"), entry.name
        assert re.search(rf"^name: {re.escape(entry.name)}$", text, re.M), entry.name
        assert f"description: {entry.description}\n" in text, entry.name
        assert len(text.splitlines()) < 150, entry.name
        local_files = {p.relative_to(root).as_posix() for p in (root / "skills" / entry.name).rglob("*.md")}
        assert local_files <= set(entry.resources), entry.name
    for resource in bundle.resources:
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", resource.text):
            if "://" in target or target.startswith("#"):
                continue
            resolved = (root / resource.name).parent.joinpath(target.split("#")[0]).resolve()
            relative = resolved.relative_to(root.resolve()).as_posix()
            assert relative in frozen, (resource.name, target)
    provenance = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    documented = {item["target"] for item in provenance["files"]}
    assert {p.relative_to(root).as_posix() for p in root.rglob("*.md")} == documented
    for upstream in provenance["upstreams"]:
        assert (root / upstream["license"]).is_file()
        assert re.fullmatch(r"[a-f0-9]{40}", upstream["commit"])
    for item in provenance["files"]:
        assert item["kind"] == "original" or item["sources"], item
        for source in item["sources"]:
            assert re.fullmatch(r"[a-f0-9]{64}", source["sha256"]), source
    print(f"Validated {len(bundle.skills)} skills, {len(bundle.resources)} resources; links/provenance OK")


if __name__ == "__main__":
    main()
