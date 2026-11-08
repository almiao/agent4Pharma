"""期刊维护参与者，允许在外层审批工作单元内原子登记或更新关注。"""

import hashlib
import sqlite3

from agent4pharma.modules.sources.venue_identity import normalize_name


def watch_venue(db: sqlite3.Connection, name: str, url: str, target_id: str, expected: str) -> str:
    """按稳定名称登记期刊；既有记录必须匹配建议生成时的状态。"""
    normalized = normalize_name(name)
    old = db.execute("SELECT id,watch_status FROM venues WHERE normalized_name=?", (normalized,)).fetchone()
    if old:
        if old["id"] != target_id or old["watch_status"] != expected:
            raise ValueError("期刊状态已变化，请重新生成建议")
        db.execute(
            "UPDATE venues SET watch_status='watch',kind='journal',homepage_url=coalesce(homepage_url,?) "
            "WHERE id=?",
            (url, old["id"]),
        )
        return old["id"]
    if target_id:
        raise ValueError("目标期刊不存在")
    identity = "venue-" + hashlib.sha256(normalized.encode()).hexdigest()[:24]
    db.execute(
        "INSERT INTO venues(id,name,normalized_name,kind,watch_status,scope_note,identifiers,homepage_url) "
        "VALUES (?,?,?,'journal','watch','由人工确认的来源维护建议登记','{}',?)",
        (identity, name, normalized, url),
    )
    return identity
