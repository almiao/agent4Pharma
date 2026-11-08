"""逐源分析实际 RSS 字段，将观察与语义提取映射保存到来源版本。"""

import argparse
import hashlib
import json
import statistics
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree

from agent4pharma.composition import build_collection_plan_catalog, open_application_storage
from agent4pharma.infrastructure.connectors.rss_signals import bibliographic_only, clean_signal
from agent4pharma.modules.sources.collection_plans import RssExtractionProfile, RssFieldMeasurement
from agent4pharma.shared.clock import utcnow


def inspect_fields(body: bytes, name: str) -> RssExtractionProfile:
    """逐字段统计存在率和长度；选择真实研究文本，不将存在率冒充摘要覆盖率。"""
    root = ElementTree.fromstring(body)
    items = [item for item in root.iter() if item.tag.rsplit("}", 1)[-1] in {"item", "entry"}][:200]
    observed = defaultdict(list)
    for item in items:
        per_item = defaultdict(list)
        for node in item:
            key = node.tag.rsplit("}", 1)[-1]
            value = "".join(node.itertext()).strip() or node.attrib.get("term", "")
            per_item[key].append(clean_signal(value))
        for key, values in per_item.items():
            observed[key].append(" ".join(values))
    measurements, text_fields = [], []
    for key, values in sorted(observed.items()):
        role = "provenance"
        if key == "title":
            role = "title"
        elif key in {"description", "summary", "content", "encoded", "abstract"}:
            content = [value for value in values if value]
            usable = [value for value in content if not bibliographic_only(value)]
            role = "semantic_text" if usable else "bibliographic_only"
            if usable:
                text_fields.append(key)
        elif key in {"category", "subject"}:
            role = "category"
        elif key in {"id", "guid", "identifier", "doi", "link", "url"}:
            role = "identity"
        measurements.append(
            RssFieldMeasurement(
                field=key,
                present=sum(bool(value) for value in values),
                total=len(items),
                median_chars=statistics.median(len(value) for value in values),
                role=role,
            )
        )
    return RssExtractionProfile(
        journal_name=name,
        text_fields=text_fields,
        measurements=measurements,
        inspected_at=utcnow(),
        evidence_sha256=hashlib.sha256(body).hexdigest(),
    )


def main() -> None:
    """成功源写入实际规则；不可用源暂停，完整观察另存为可审阅产物。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, default=Path("data/reports/rss-triage/audit"))
    args = parser.parse_args()
    rows = json.loads((args.audit / "audit.json").read_text(encoding="utf-8"))
    store = open_application_storage(Path("data"))
    store.close()
    catalog = build_collection_plan_catalog(Path("data"))
    by_name = {plan.name: plan for plan in catalog.list_plans()}
    profiles = []
    for row in rows:
        plan = by_name.get(row["name"])
        if plan is None:
            continue
        if not row["usable"]:
            if plan.enabled:
                catalog.save(plan.model_copy(update={"enabled": False}), expected_hash=plan.fingerprint())
            continue
        path = args.audit / (hashlib.sha256(row["name"].encode()).hexdigest()[:16] + ".xml")
        profile = inspect_fields(path.read_bytes(), row["name"])
        plan = catalog.save(
            plan.model_copy(update={"rss_profile": profile, "enabled": True}),
            expected_hash=plan.fingerprint(),
        )
        profiles.append({"source_id": plan.source_id, "name": plan.name, "profile": profile.model_dump()})
    out = args.audit.parent / "field-profiles.json"
    out.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Saved source extraction profiles:", len(profiles), flush=True)


if __name__ == "__main__":
    main()
