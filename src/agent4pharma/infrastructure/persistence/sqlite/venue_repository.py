"""载体目录存取：维护期刊/会议身份、别名、指标与出处，避免错误合并已审核载体。"""

from __future__ import annotations

import hashlib
import html
import json

from agent4pharma.modules.sources.venue_identity import normalize_name, venue_id
from agent4pharma.shared.serialization import dump


def merge_observed_label(store, old_id: str, canonical_id: str):
    """Only a curator's explicit alias can merge a previously unreviewed raw label."""
    old = store.db.execute("SELECT * FROM venues WHERE id=?", (old_id,)).fetchone()
    metric = store.db.execute(
        "SELECT 1 FROM journal_metric_observations WHERE venue_id=?",
        (old_id,),
    ).fetchone()
    evidence = store.db.execute("SELECT * FROM venue_evidence WHERE venue_id=?", (old_id,)).fetchall()
    if (
        old["kind"] != "unknown"
        or old["watch_status"] != "unreviewed"
        or metric
        or old["scope_note"]
        or json.loads(old["identifiers"])
        or any(row["source_kind"] != "biotender_discovery" for row in evidence)
    ):
        raise ValueError("Alias collision with a reviewed/enriched venue; explicit review required")
    for row in evidence:
        add_evidence(
            store,
            canonical_id,
            row["source_kind"],
            row["source_url"],
            row["snapshot_ref"],
            json.loads(row["payload"]),
            row["observed_at"],
            row["record_count"],
        )
    store.db.execute("UPDATE venue_aliases SET venue_id=? WHERE venue_id=?", (canonical_id, old_id))
    store.db.execute("DELETE FROM venue_evidence WHERE venue_id=?", (old_id,))
    store.db.execute("DELETE FROM venues WHERE id=?", (old_id,))


def add_venue(store, entry: dict, *, reviewed: bool = True) -> str:
    """按规范化名称/显式别名保存载体；别名冲突需人工核对，不能合并已审核载体。"""
    name = html.unescape(entry["name"]).strip()
    normalized = normalize_name(name)
    if not normalized:
        raise ValueError("Venue name must not be blank")
    match = store.db.execute(
        "SELECT venue_id FROM venue_aliases WHERE normalized_alias=?",
        (normalized,),
    ).fetchone()
    identity = match[0] if match else venue_id(name)
    store.db.execute(
        "INSERT OR IGNORE INTO venues(id,name,normalized_name,kind,watch_status,scope_note,identifiers) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            identity,
            name,
            normalized,
            entry.get("kind", "unknown"),
            entry.get("watch_status", "unreviewed"),
            entry.get("scope_note", ""),
            dump(entry.get("identifiers", {})),
        ),
    )
    if reviewed:
        store.db.execute(
            "UPDATE venues SET kind=?,watch_status=?,scope_note=? WHERE id=?",
            (
                entry.get("kind", "journal"),
                entry.get("watch_status", "watch"),
                entry.get("scope_note", ""),
                identity,
            ),
        )
    for alias in [name, *entry.get("aliases", [])]:
        key = normalize_name(alias)
        owner = store.db.execute(
            "SELECT venue_id FROM venue_aliases WHERE normalized_alias=?",
            (key,),
        ).fetchone()
        if owner and owner[0] != identity:
            if not reviewed:
                raise ValueError(f"Alias collision: {alias}")
            merge_observed_label(store, owner[0], identity)
        store.db.execute(
            "INSERT OR IGNORE INTO venue_aliases VALUES (?,?,?,?)",
            (
                key,
                identity,
                alias,
                "curated" if reviewed else "observed_name_only",
            ),
        )
    return identity


def add_evidence(
    store,
    identity: str,
    kind: str,
    url: str,
    snapshot: str,
    payload: dict,
    observed_at: str,
    count: int | None = None,
):
    """按出处内容的稳定身份保存载体观察，重复观察不重复插入；不自行提交。"""
    key = hashlib.sha256(f"{identity}:{kind}:{url}:{snapshot}:{dump(payload)}".encode()).hexdigest()
    store.db.execute(
        "INSERT OR IGNORE INTO venue_evidence VALUES (?,?,?,?,?,?,?,?)",
        (
            key,
            identity,
            kind,
            url,
            snapshot,
            count,
            dump(payload),
            observed_at,
        ),
    )


def list_catalog(store, status: str | None = None, limit: int = 200) -> list[dict]:
    """按关注状态查询载体与最近指标；缺失指标保持 None，不转换为零。"""
    if not 1 <= limit <= 20000:
        raise ValueError("limit must be 1..20000")
    rows = store.db.execute(
        "SELECT * FROM venues " + ("WHERE watch_status=? " if status else "") + "ORDER BY name LIMIT ?",
        (status, limit) if status else (limit,),
    ).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        metric = store.db.execute(
            "SELECT value,metric_year,verification_state,source_url FROM journal_metric_observations "
            "WHERE venue_id=? ORDER BY metric_year DESC,observed_at DESC,id LIMIT 1",
            (row["id"],),
        ).fetchone()
        item["metric"] = dict(metric) if metric else None
        item["identifiers"] = json.loads(item["identifiers"])
        output.append(item)
    return output
