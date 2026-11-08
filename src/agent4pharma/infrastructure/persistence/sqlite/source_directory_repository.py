"""共享来源目录存取：原子导入隔离 demo，并提供来源和资料关系的只读投影。"""

from __future__ import annotations

import json
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.venue_repository import add_venue
from agent4pharma.modules.sources.venue_identity import normalize_name
from agent4pharma.shared.serialization import dump


def demo_endpoints(store) -> list[dict]:
    """查询 demo 来源入口及运营机构关系，供报告展示真实数据库状态。"""
    return [
        dict(row)
        for row in store.db.execute(
            "SELECT s.name,s.source_kind,o.name AS organization,e.url,e.access_method,e.metadata,e.enabled "
            "FROM source_endpoints e JOIN sources s ON s.id=e.source_id "
            "LEFT JOIN organizations o ON o.id=s.organization_id "
            "WHERE json_extract(e.metadata,'$.dataset')='demo' ORDER BY s.name,e.url"
        )
    ]


def upsert(db, table: str, row: dict):
    """Callers supply table and column names from this module, never page text."""
    columns = list(row)
    updates = ",".join(f"{key}=excluded.{key}" for key in columns if key != "id")
    db.execute(
        f"INSERT INTO {table}({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}",
        list(row.values()),
    )


def import_demo(store, seed_path: Path) -> dict:
    """原子导入 demo 命名空间的目录和资料；所有新入口关闭，不进入生产发现批次。"""
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    for group in ("organizations", "conference_editions", "sources", "endpoints"):
        if any(not item["id"].startswith("demo-") for item in seed.get(group, [])):
            raise ValueError("Demo directory IDs must use the demo- namespace")
    source_ids = {item["id"] for item in seed["sources"]}
    if any(item["source_id"] not in source_ids for item in seed["endpoints"] + seed["materials"]):
        raise ValueError("Every demo material/endpoint must reference a declared source")
    # Every helper participates in this same transaction and never commits itself.
    with store.db:
        _import_organizations(store, seed)
        venues = _import_venues(store, seed)
        _import_editions(store, seed, venues)
        _import_sources(store, seed, venues)
        _import_endpoints(store, seed)
        store.materials.import_demo_materials(seed, venues)
    return inspect_demo(store)


def inspect_demo(store) -> dict:
    """返回 demo 数据集的目录、资料和来源关系投影，不执行网络请求。"""
    sources = [
        dict(r)
        for r in store.db.execute(
            "SELECT s.id,s.name,s.source_kind,s.publisher_name,o.name AS organization,s.status "
            "FROM sources s LEFT JOIN organizations o ON o.id=s.organization_id "
            "WHERE json_extract(s.descriptor,'$.dataset')='demo' ORDER BY s.id"
        )
    ]
    materials = [
        dict(r)
        for r in store.db.execute(
            "SELECT m.id,m.title,m.material_type,m.canonical_url,o.name AS publisher,v.name AS venue,"
            "m.conference_edition_id FROM materials m "
            "LEFT JOIN organizations o ON o.id=m.publisher_organization_id "
            "LEFT JOIN venues v ON v.id=m.venue_id WHERE m.dataset='demo' ORDER BY m.canonical_url"
        )
    ]
    records = [
        dict(r)
        for r in store.db.execute(
            "SELECT sr.material_id,sr.source_id,sr.metadata FROM source_records sr "
            "JOIN materials m ON m.id=sr.material_id WHERE m.dataset='demo' ORDER BY sr.observed_at,sr.id"
        )
    ]
    for row in records:
        row["metadata"] = json.loads(row["metadata"])
    return {
        "organization_count": store.db.execute(
            "SELECT count(*) FROM organizations WHERE json_extract(metadata,'$.dataset')='demo'"
        ).fetchone()[0],
        "endpoint_count": store.db.execute(
            "SELECT count(*) FROM source_endpoints WHERE json_extract(metadata,'$.dataset')='demo'"
        ).fetchone()[0],
        "source_count": len(sources),
        "material_count": len(materials),
        "record_count": len(records),
        "sources": sources,
        "materials": materials,
        "records": records,
    }


def _import_organizations(store, seed):
    for item in seed.get("organizations", []):
        upsert(
            store.db,
            "organizations",
            {
                "id": item["id"],
                "name": item["name"],
                "kind": item["kind"],
                "homepage_url": item.get("homepage_url"),
                "parent_id": item.get("parent_id"),
                "watch_status": "candidate",
                "metadata": dump({"dataset": "demo"}),
            },
        )


def _import_venues(store, seed):
    venues = {}
    for item in seed.get("venues", []):
        known = store.db.execute(
            "SELECT venue_id FROM venue_aliases WHERE normalized_alias=?", (normalize_name(item["name"]),)
        ).fetchone()
        vid = known[0] if known else add_venue(store, item, reviewed=False)
        store.db.execute("UPDATE venues SET kind=? WHERE id=? AND kind='unknown'", (item["kind"], vid))
        venues[item["key"]] = vid
        if item.get("homepage_url"):
            store.db.execute("UPDATE venues SET homepage_url=? WHERE id=?", (item["homepage_url"], vid))
    return venues


def _import_editions(store, seed, venues):
    for item in seed.get("conference_editions", []):
        upsert(
            store.db,
            "conference_editions",
            {
                "id": item["id"],
                "venue_id": venues[item["venue_key"]],
                "year": item["year"],
                "name": item["name"],
                "homepage_url": item.get("homepage_url"),
                "start_date": item.get("start_date"),
                "end_date": item.get("end_date"),
            },
        )


def _import_sources(store, seed, venues):
    for item in seed["sources"]:
        upsert(
            store.db,
            "sources",
            {
                "id": item["id"],
                "descriptor": dump({"dataset": "demo", "note": item.get("note")}),
                "name": item["name"],
                "source_kind": item["source_kind"],
                "organization_id": item.get("organization_id"),
                "venue_id": venues.get(item.get("venue_key")),
                "homepage_url": item.get("homepage_url"),
                "status": "candidate",
                "platform": item.get("platform"),
                "external_id": item.get("external_id"),
                "publisher_name": item.get("publisher_name"),
            },
        )


def _import_endpoints(store, seed):
    for item in seed["endpoints"]:
        upsert(
            store.db,
            "source_endpoints",
            {
                "id": item["id"],
                "source_id": item["source_id"],
                "edition_id": item.get("edition_id"),
                "url": item["url"],
                "access_method": item["access_method"],
                "connector_key": None,
                "access_config_id": None,
                "enabled": 0,
                "metadata": dump(
                    {
                        "dataset": "demo",
                        "access_state": item["access_state"],
                        "capability": item.get("capability", "candidate_entrypoint"),
                    }
                ),
            },
        )
