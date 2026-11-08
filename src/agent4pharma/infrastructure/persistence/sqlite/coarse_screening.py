"""采集后的统一预选入口：全部资料交本地 Decider，关键词只用于编译查询。"""

import hashlib
import sqlite3

from agent4pharma.runtime.settings import ApplicationSettings
from agent4pharma.shared.serialization import dump


def policy_hash(policy: dict) -> str:
    """稳定身份覆盖来源查询、粗筛和情报版本，不包含业务日期。"""
    return hashlib.sha256(dump(policy).encode()).hexdigest()


def assess_coarse(db: sqlite3.Connection, batch_id: str, config: ApplicationSettings) -> None:
    """不再用关键词过滤资料；启用本地判别时全部待判，否则直送情报判断。

    关键词表只用于编译上游查询和人工预览，不能决定一条已采集资料是否进入
    详情分析。未启用本地 Decider 时没有可用的判别器，因此全部保留给情报
    Agent，避免把缺省配置悄悄降级成词表过滤。
    """
    triage_enabled = config.triage.enabled
    rows = db.execute(
        "SELECT id FROM discovery_batch_items WHERE batch_id=? AND state='pending' ORDER BY id",
        (batch_id,),
    ).fetchall()
    with db:
        for row in rows:
            assessment = {
                "method": "local_triage_pending" if triage_enabled else "semantic_passthrough",
                "policy_hash": policy_hash(config.snapshot()),
                "selected": False,
                "reason": "等待本地 Decider 二分类" if triage_enabled else "未启用本地判别，直接进入情报判断",
            }
            if not triage_enabled:
                assessment["local_route"] = "analyze"
            db.execute(
                "UPDATE discovery_batch_items SET state=?,assessment=?,assessment_revision=1 WHERE id=?",
                (
                    "awaiting_triage" if triage_enabled else "awaiting_intelligence",
                    dump(assessment),
                    row["id"],
                ),
            )
