"""来源模块的通用采集配置仓储；配置、目录和版本历史在一个工作单元保存。"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction
from agent4pharma.modules.sources.collection_plans import CollectionPlan
from agent4pharma.shared.clock import utcnow
from agent4pharma.shared.serialization import dump


@dataclass
class CollectionPlanRepository:
    """短连接读取计划，写入时保留同主页的既有来源 ID 和机构关系。"""

    root: Path

    def get(self, source_id: str) -> CollectionPlan:
        """读取单个数据库计划，不依赖代码库中的站点文件。"""
        with transaction(self.root) as db:
            row = db.execute(
                "SELECT definition FROM source_collection_plans WHERE source_id=?", (source_id,)
            ).fetchone()
        if row is None:
            raise KeyError("来源采集计划不存在")
        return CollectionPlan.model_validate_json(row[0])

    def save(self, plan: CollectionPlan, *, expected_hash: str | None = None) -> CollectionPlan:
        """按 enabled 保存计划；更新校验预期版本，同一未改定义可幂等重放。"""
        with transaction(self.root) as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT id,descriptor FROM sources WHERE id=? OR homepage_url=? ORDER BY id LIMIT 1",
                (plan.source_id, plan.homepage_url),
            ).fetchone()
            source_id = existing["id"] if existing else plan.source_id
            plan = plan.model_copy(update={"source_id": source_id})
            current = db.execute(
                "SELECT content_hash FROM source_collection_plans WHERE source_id=?", (source_id,)
            ).fetchone()
            if current and expected_hash is None and current[0] == plan.fingerprint():
                return plan
            if (current and current[0] != expected_hash) or (not current and expected_hash is not None):
                raise ValueError("来源采集配置版本已变化；更新需提交当前 expected_hash")
            descriptor = json.loads(existing["descriptor"]) if existing else {}
            if descriptor.get("dataset") == "demo":
                descriptor["promoted_from_demo"] = True
            descriptor["dataset"] = "main"
            db.execute(
                "INSERT INTO sources(id,descriptor,name,source_kind,homepage_url,status) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "descriptor=excluded.descriptor,name=excluded.name,homepage_url=excluded.homepage_url,"
                "status=excluded.status,management_revision=sources.management_revision+1",
                (
                    source_id,
                    dump(descriptor),
                    plan.name,
                    plan.source_kind,
                    plan.homepage_url,
                    "watch" if plan.enabled else "paused",
                ),
            )
            definition, version = plan.model_dump_json(), plan.fingerprint()
            db.execute(
                "INSERT INTO source_collection_plans(source_id,definition,content_hash,enabled) "
                "VALUES (?,?,?,?) "
                "ON CONFLICT(source_id) DO UPDATE SET definition=excluded.definition,"
                "content_hash=excluded.content_hash,enabled=excluded.enabled",
                (source_id, definition, version, int(plan.enabled)),
            )
            db.execute(
                "INSERT OR IGNORE INTO source_collection_plan_versions"
                "(source_id,content_hash,definition,created_at) VALUES (?,?,?,?)",
                (source_id, version, definition, utcnow()),
            )
            # 只关闭本能力曾拥有的入口，普通目录和旧 RSS 配置不被覆盖。
            db.execute(
                "UPDATE source_endpoints SET enabled=0 WHERE source_id=? "
                "AND connector_key='configured-web-v1'",
                (source_id,),
            )
            for entry in plan.entries:
                endpoint_id = (
                    "endpoint-" + hashlib.sha256(f"{source_id}:{entry.url}".encode()).hexdigest()[:24]
                )
                db.execute(
                    "INSERT INTO source_endpoints"
                    "(id,source_id,url,access_method,connector_key,enabled,metadata) "
                    "VALUES (?,?,?,?,'configured-web-v1',?,?) ON CONFLICT(source_id,url) DO UPDATE SET "
                    "access_method=excluded.access_method,connector_key=excluded.connector_key,"
                    "enabled=excluded.enabled,metadata=excluded.metadata",
                    (
                        endpoint_id,
                        source_id,
                        entry.url,
                        entry.kind,
                        int(plan.enabled),
                        entry.model_dump_json(),
                    ),
                )
        return plan

    def list_plans(self, *, enabled_only: bool = False) -> list[CollectionPlan]:
        """仅关注且配置启用的来源进入调度，暂停来源立即退出新运行。"""
        with transaction(self.root) as db:
            rows = db.execute(
                "SELECT p.definition FROM source_collection_plans p JOIN sources s ON s.id=p.source_id "
                + ("WHERE p.enabled=1 AND s.status='watch' " if enabled_only else "")
                + "ORDER BY p.source_id"
            ).fetchall()
        return [CollectionPlan.model_validate_json(row[0]) for row in rows]
