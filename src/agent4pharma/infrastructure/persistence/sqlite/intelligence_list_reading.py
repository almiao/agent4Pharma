"""情报列表批量投影；只读取列表字段及证据状态，不加载政策、提示词或原文。"""

import json
import sqlite3

from agent4pharma.modules.discovery.evidence_status import evidence_states
from agent4pharma.modules.discovery.intelligence_contracts import (
    IntelligenceEvidence,
    IntelligenceTaskSummary,
)

# text仅保留是否存在正文，供已有领域状态规则判断，不传输全文。
_STATE = """json_object('id',coalesce(json_extract(VALUE,'$.id'),'evidence'),
 'abstract_status',json_extract(VALUE,'$.abstract_status'),
 'origin',coalesce(json_extract(VALUE,'$.origin'),'metadata'),
 'status',coalesce(json_extract(VALUE,'$.status'),'available'),
 'text',CASE WHEN length(trim(coalesce(json_extract(VALUE,'$.text'),''),
 char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,
 8199,8200,8201,8202,8232,8233,8239,8287,12288)))>0 THEN 'x' ELSE '' END)"""


def run_task_summaries(db: sqlite3.Connection, parent_ref: str) -> list[IntelligenceTaskSummary]:
    """批量读取准确批次的原生和明确导入任务，保持历史父身份及证据状态优先级。"""
    rows = db.execute(
        "SELECT id,status,error,updated_at,"
        "json_extract(request,'$.material_ref') AS material_ref,"
        "coalesce(json_extract(request,'$.evidence[0].title'),'') AS title,"
        "json_extract(request,'$.mode') AS mode,"
        "coalesce(json_extract(request,'$.stage'),'legacy') AS stage,"
        "coalesce(json_extract(request,'$.selection_ref'),'') AS selection_ref,"
        "coalesce(json_extract(request,'$.parent_ref'),'') AS parent_ref,"
        "json_extract(request,'$.evidence[0].id') AS source_record_id,"
        "coalesce(json_extract(result,'$.summary'),'') AS summary,"
        "coalesce(json_extract(result,'$.disposition'),'') AS disposition,"
        "(SELECT json_group_array(" + _STATE.replace("VALUE", "e.value") + ") "
        "FROM json_each(request,'$.evidence') e) AS states "
        "FROM intelligence_tasks WHERE json_extract(request,'$.parent_ref')=? OR EXISTS "
        "(SELECT 1 FROM discovery_batch_items i WHERE i.batch_id=? "
        "AND json_extract(i.assessment,'$.intelligence_task')=intelligence_tasks.id "
        "AND i.material_id=json_extract(intelligence_tasks.request,'$.material_ref') "
        "AND i.source_record_id=json_extract(intelligence_tasks.request,'$.evidence[0].id'))",
        (parent_ref, parent_ref),
    ).fetchall()
    evidence = {r["id"]: [IntelligenceEvidence(**e) for e in json.loads(r["states"])] for r in rows}
    identities = list(evidence)
    for start in range(0, len(identities), 500):
        chunk = identities[start : start + 500]
        readings = db.execute(
            "SELECT task_id," + _STATE.replace("VALUE", "payload") + " AS state "
            "FROM intelligence_readings WHERE task_id IN (" + ",".join("?" for _ in chunk) + ")",
            chunk,
        )
        for row in readings:
            evidence[row["task_id"]].append(IntelligenceEvidence.model_validate_json(row["state"]))
    summaries = []
    for row in rows:
        abstract, fulltext = evidence_states(evidence[row["id"]])
        values = dict(row)
        values.pop("states")
        summaries.append(IntelligenceTaskSummary(**values, abstract_state=abstract, fulltext_state=fulltext))
    return summaries
