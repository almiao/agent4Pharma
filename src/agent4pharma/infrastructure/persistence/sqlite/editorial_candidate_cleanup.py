"""人工授权的未立项候选清理；保留原始资料、评估和当前制作任务。"""

import json
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction


def remove_other_uncommissioned_candidates(root: Path, keep_task_id: str) -> int:
    """仅在没有其他制作历史时清理候选；不备份，校验与删除同一事务提交。

    调用方必须取得人工删除授权。保留候选必须唯一，且不能存在其他候选的
    制作任务或选题，避免删除已立项对象的来源。条件不满足时整笔回滚。
    """
    with transaction(root) as db:
        db.execute("BEGIN IMMEDIATE")
        kept = db.execute("SELECT id FROM editorial_candidates WHERE task_id=?", (keep_task_id,)).fetchall()
        if len(kept) != 1:
            raise ValueError("需保留的全文候选必须唯一")
        if db.execute("SELECT 1 FROM production_stories LIMIT 1").fetchone():
            raise ValueError("已有立项记录，不能按未立项候选清理")
        for row in db.execute("SELECT payload FROM production_tasks"):
            task = json.loads(row[0])
            context = json.loads(task["input_json"])
            if any(candidate["id"] != kept[0][0] for candidate in context["candidates"]):
                raise ValueError("其他候选已有制作任务，不能清理")
            if task["status"] == "running":
                raise ValueError("制作任务运行中，暂不清理")
        return db.execute("DELETE FROM editorial_candidates WHERE id<>?", (kept[0][0],)).rowcount
