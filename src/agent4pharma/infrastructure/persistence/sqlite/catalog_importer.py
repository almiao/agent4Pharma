"""期刊目录导入工作单元：在同一事务中合并整理目录、观察标签与出处记录。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.venue_repository import add_evidence, add_venue
from agent4pharma.modules.sources.venue_identity import normalize_name
from agent4pharma.shared.serialization import dump


def import_catalog(store, seed_path: Path, inventory_path: Path) -> dict:
    """Idempotent import of curated seeds and all observed venue strings.

    A BioTender venue field is evidence of a label, not proof of a journal or JIF.
    """

    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    with store.db:
        baseline, curated = _import_curated(store, seed)
        additions, overlaps = _import_observed(store, seed, inventory, baseline, curated)
        _import_navigation(store, seed, inventory_path, curated)

    summary = {
        "database": str(store.root / "discovery.sqlite3"),
        "baseline_28": len(baseline),
        "curated_entries": len(curated),
        "biotender_records_scanned": inventory["record_count"],
        "biotender_raw_venue_labels": len(inventory["venues"]),
        "biotender_missing_venue": inventory["missing_venue_count"],
        "overlap_with_baseline": _combine(overlaps),
        "additions_to_baseline": _combine(additions),
        "total_venues_in_database": store.db.execute("SELECT count(*) FROM venues").fetchone()[0],
        "metric_observations": store.db.execute(
            "SELECT count(*) FROM journal_metric_observations"
        ).fetchone()[0],
        "snapshot_generated_at": inventory["snapshot_generated_at"],
        "scope": "venue inventory only; no external papers imported and no IF filter activated",
    }
    return summary


def _import_curated(store, seed):
    baseline, curated = set(), {}
    for entry in seed["venues"]:
        identity = add_venue(store, entry)
        curated[identity] = entry
        if entry.get("baseline_28"):
            baseline.add(identity)
        add_evidence(
            store,
            identity,
            "curated_watchlist",
            entry["reference_url"],
            seed["version"],
            {"baseline_28": entry.get("baseline_28", False), "reason": entry.get("scope_note", "")},
            seed["observed_at"],
        )
        if metric := entry.get("metric"):
            if metric.get("metric_year") is None and metric["verification_state"] != "year_pending":
                raise ValueError("Missing metric year must be explicit")
            key = hashlib.sha256(dump({"venue_id": identity, **metric}).encode()).hexdigest()
            store.db.execute(
                "INSERT OR IGNORE INTO journal_metric_observations VALUES (?,?,?,?,?,?,?,?)",
                (
                    key,
                    identity,
                    "JIF_2Y",
                    metric["value"],
                    metric.get("metric_year"),
                    metric["source_url"],
                    seed["observed_at"],
                    metric["verification_state"],
                ),
            )
    return baseline, curated


def _import_observed(store, seed, inventory, baseline, curated):
    additions, overlaps = [], []
    for item in inventory["venues"]:
        name = item["name"]
        identity = add_venue(store, {"name": name}, reviewed=False)
        for alias in item.get("variants", []):
            key = normalize_name(alias)
            owner = store.db.execute(
                "SELECT venue_id FROM venue_aliases WHERE normalized_alias=?",
                (key,),
            ).fetchone()
            if owner and owner[0] != identity:
                raise ValueError(f"Observed alias collision: {alias}")
            store.db.execute(
                "INSERT OR IGNORE INTO venue_aliases VALUES (?,?,?,?)",
                (
                    key,
                    identity,
                    alias,
                    "observed_name_only",
                ),
            )
        add_evidence(
            store,
            identity,
            "biotender_discovery",
            inventory["source_url"],
            inventory["snapshot_generated_at"],
            item,
            seed["observed_at"],
            item["count"],
        )
        record = {
            "venue_id": identity,
            "name": store.db.execute(
                "SELECT name FROM venues WHERE id=?",
                (identity,),
            ).fetchone()[0],
            "count": item["count"],
            "topics": item.get("topics", {}),
            "curated": identity in curated,
            "kind": store.db.execute(
                "SELECT kind FROM venues WHERE id=?",
                (identity,),
            ).fetchone()[0],
        }
        (overlaps if identity in baseline else additions).append(record)
    return additions, overlaps


def _import_navigation(store, seed, inventory_path, curated):
    # The navigation index supplies title mentions, not original-paper citations.
    if navigation_path := seed.get("navigation_snapshot"):
        path = inventory_path.parent.parent / navigation_path
        if path.exists():
            links = json.loads(path.read_text(encoding="utf-8"))
            for identity, entry in curated.items():
                names = [entry["name"], *entry.get("aliases", [])]
                matches = []
                for link in links:
                    title = normalize_name(link.get("title", ""))
                    if any(
                        re.search(r"(?<!\w)" + re.escape(normalize_name(n)) + r"(?!\w)", title)
                        for n in names
                        if len(n) >= 4
                    ):
                        matches.append({"title": link["title"], "url": link["url"]})
                if matches:
                    add_evidence(
                        store,
                        identity,
                        "biotender_navigation_title",
                        seed["navigation_url"],
                        seed["navigation_revision"],
                        {
                            "matches": matches,
                            "caveat": "title mention only; not a verified publication venue",
                        },
                        seed["observed_at"],
                        len(matches),
                    )


def _combine(rows):
    combined = {}
    for row in rows:
        item = combined.setdefault(row["venue_id"], {**row, "count": 0, "topics": {}})
        item["count"] += row["count"]
        for topic, count in row["topics"].items():
            item["topics"][topic] = item["topics"].get(topic, 0) + count
    return sorted(combined.values(), key=lambda item: -item["count"])
