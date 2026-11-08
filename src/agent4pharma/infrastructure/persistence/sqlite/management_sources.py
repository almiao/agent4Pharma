"""复用共享来源目录的管理存取，保留机构关系与编辑版本。"""

import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction
from agent4pharma.modules.sources.management import (
    ManagedSource,
    SourceCollectionAvailability,
    SourceDirectoryFilters,
    SourceEdit,
)
from agent4pharma.shared.clock import utcnow
from agent4pharma.shared.serialization import dump
from agent4pharma.shared.url_validation import public_reference_url


def _check_url(value: str) -> None:
    public_reference_url(value)


@dataclass
class ManagedSourceRepository:
    """来源和入口编辑在同一事务内，旧元数据与关系不因编辑被重建。"""

    root: Path
    connection: sqlite3.Connection | None = None

    def promote_followed_source(self, source_id: str, job_id: str) -> None:
        """确认后的来源退出 demo 身份，仅实际读取到条目的 RSS 可开启通用连接器。"""
        from agent4pharma.infrastructure.persistence.sqlite.investigation_evidence import EvidenceRepository

        pages = EvidenceRepository(self.root).list_evidence(job_id)
        verified = {p.requested_url for p in pages if p.status == "fetched" and p.feed_entries}
        with transaction(self.root, self.connection) as db:
            row = db.execute("SELECT descriptor FROM sources WHERE id=?", (source_id,)).fetchone()
            meta = json.loads(row[0])
            if meta.get("dataset") == "demo":
                meta["promoted_from_demo"] = True
            meta["dataset"] = "main"
            db.execute("UPDATE sources SET descriptor=?,status='watch' WHERE id=?", (dump(meta), source_id))
            for endpoint in db.execute(
                "SELECT id,url,access_method FROM source_endpoints WHERE source_id=?", (source_id,)
            ):
                if endpoint["access_method"] == "rss" and endpoint["url"] in verified:
                    db.execute(
                        "UPDATE source_endpoints SET enabled=1,connector_key='rss-v1' WHERE id=?",
                        (endpoint["id"],),
                    )

    def active_feeds(self) -> list[ManagedSource]:
        """读取已确认关注且已启用通用 RSS 的实际入口，非支持入口不进入调度。"""
        with transaction(self.root, self.connection) as db:
            entries = [
                dict(r)
                for r in db.execute(
                    "SELECT s.id,e.id AS endpoint_id,e.url FROM sources s JOIN source_endpoints e "
                    "ON e.source_id=s.id WHERE s.status='watch' AND e.enabled=1 AND e.connector_key='rss-v1' "
                    "ORDER BY e.id"
                )
            ]
        result = {}
        for entry in entries:
            source = next(s for s in self.list_sources(entry["id"]) if s.id == entry["id"])
            result.setdefault(
                source.id,
                source.model_copy(
                    update={
                        "endpoint_id": entry["endpoint_id"],
                        "endpoint_url": entry["url"],
                        "access_method": "rss",
                        "connector_key": "rss-v1",
                    }
                ),
            )
        return list(result.values())

    def source_kinds(self) -> list[str]:
        """读取完整目录类型，缺失类型与来源投影一致地归为资料平台。"""
        with transaction(self.root, self.connection) as db:
            return [
                row[0]
                for row in db.execute(
                    "SELECT DISTINCT coalesce(nullif(source_kind,''),'repository_platform') "
                    "AS kind FROM sources ORDER BY kind"
                )
            ]

    def list_sources(
        self, search: str = "", limit: int = 100, *, filters: SourceDirectoryFilters | None = None
    ) -> list[ManagedSource]:
        """查询来源及首入口；筛选在数据库分页前执行，默认保留原查询行为。"""
        if not 1 <= limit <= 200:
            raise ValueError("limit 必须为 1..200")
        filters = filters or SourceDirectoryFilters()
        with transaction(self.root, self.connection) as db:
            rows = db.execute(
                "SELECT s.*,o.name AS organization,e.id AS endpoint_id,e.url AS endpoint_url,"
                "e.access_method,e.connector_key,(SELECT count(*) FROM source_records r "
                "WHERE r.source_id=s.id) AS material_count FROM sources s "
                "LEFT JOIN organizations o ON o.id=s.organization_id "
                "LEFT JOIN source_endpoints e ON e.id=coalesce((SELECT ep.id FROM source_endpoints ep "
                "JOIN source_collection_plans p ON p.source_id=ep.source_id WHERE ep.source_id=s.id "
                "AND ep.url=json_extract(p.definition,'$.entries[0].url') ORDER BY ep.id LIMIT 1),"
                "(SELECT id FROM source_endpoints WHERE source_id=s.id ORDER BY id LIMIT 1)) "
                "WHERE (coalesce(s.name,s.id) LIKE ? OR s.id LIKE ? "
                "OR s.homepage_url LIKE ? OR e.url LIKE ?) "
                "AND (?='' OR coalesce(nullif(s.source_kind,''),'repository_platform')=?) "
                "AND (?='' OR s.status=?) "
                "AND (?='' OR coalesce(json_extract(s.descriptor,'$.collection_availability.status'),"
                "'unverified')=?) ORDER BY s.name,s.id LIMIT ? OFFSET ?",
                (
                    *([f"%{search}%"] * 4),
                    filters.kind,
                    filters.kind,
                    filters.status,
                    filters.status,
                    filters.availability,
                    filters.availability,
                    limit,
                    filters.offset,
                ),
            ).fetchall()
        result = []
        for row in rows:
            meta = json.loads(row["descriptor"])
            status = row["status"] if row["status"] in {"candidate", "watch", "paused"} else "candidate"
            result.append(
                ManagedSource(
                    id=row["id"],
                    name=row["name"] or row["id"],
                    source_kind=row["source_kind"] or "repository_platform",
                    homepage_url=row["homepage_url"] or "",
                    endpoint_url=row["endpoint_url"] or "",
                    access_method=row["access_method"] or "html",
                    connector_key=row["connector_key"] or "",
                    status=status,
                    notes=meta.get("maintenance_notes", ""),
                    scope_verified=meta.get("scope_verified", False),
                    revision=row["management_revision"],
                    endpoint_id=row["endpoint_id"],
                    organization=row["organization"],
                    material_count=row["material_count"],
                    dataset=meta.get("dataset", "main"),
                    collection_availability=meta.get("collection_availability"),
                )
            )
        return result

    def record_collection_availability(
        self, source_id: str, assessment: SourceCollectionAvailability, expected_revision: int
    ) -> None:
        """乐观锁保存逐源复核，保留关注状态、入口启用状态和历史任务事实。

        新复核替换当前标签，旧复核留在审计中；拒绝用较早的证据覆盖较新的复核。
        """
        with transaction(self.root, self.connection) as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT descriptor,management_revision FROM sources WHERE id=?", (source_id,)
            ).fetchone()
            if row is None:
                raise KeyError("来源不存在")
            if row["management_revision"] != expected_revision:
                raise ValueError("来源已被修改，请刷新后再复核")
            meta = json.loads(row["descriptor"])
            previous = meta.get("collection_availability")
            if (
                previous
                and SourceCollectionAvailability.model_validate(previous).checked_at > assessment.checked_at
            ):
                raise ValueError("不能用较早复核覆盖最新复核")
            meta["collection_availability"] = assessment.model_dump(mode="json")
            db.execute(
                "UPDATE sources SET descriptor=?,management_revision=management_revision+1 WHERE id=?",
                (dump(meta), source_id),
            )
            db.execute(
                "INSERT INTO maintenance_audit(kind,entity_id,action,detail,created_at) VALUES (?,?,?,?,?)",
                (
                    "source",
                    source_id,
                    "collection_recheck",
                    dump(assessment.model_dump(mode="json")),
                    utcnow(),
                ),
            )

    def save_source(self, change: SourceEdit, source_id: str | None = None) -> ManagedSource:
        """新增或乐观锁更新目录，入口始终不因管理编辑自动启用采集。"""
        _check_url(change.homepage_url)
        _check_url(change.endpoint_url)
        if not change.name.strip():
            raise ValueError("来源名称不能为空")
        identity, now = source_id or "source-" + uuid.uuid4().hex, utcnow()
        with transaction(self.root, self.connection) as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT descriptor,management_revision FROM sources WHERE id=?", (identity,)
            ).fetchone()
            if source_id and old is None:
                raise KeyError("来源不存在")
            if old and change.revision != old["management_revision"]:
                raise ValueError("来源已被修改，请刷新后再编辑")
            descriptor = json.loads(old["descriptor"]) if old else {}
            descriptor.update(maintenance_notes=change.notes, scope_verified=change.scope_verified)
            revision = old["management_revision"] + 1 if old else 1
            db.execute(
                "INSERT INTO sources(id,descriptor,name,source_kind,homepage_url,status,management_revision) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET descriptor=excluded.descriptor,"
                "name=excluded.name,source_kind=excluded.source_kind,homepage_url=excluded.homepage_url,"
                "status=excluded.status,management_revision=excluded.management_revision",
                (
                    identity,
                    dump(descriptor),
                    change.name.strip(),
                    change.source_kind,
                    change.homepage_url or None,
                    change.status,
                    revision,
                ),
            )
            if change.endpoint_url:
                endpoint = db.execute(
                    "SELECT id FROM source_endpoints WHERE source_id=? ORDER BY id LIMIT 1", (identity,)
                ).fetchone()
                endpoint_id = endpoint[0] if endpoint else "endpoint-" + uuid.uuid4().hex
                db.execute(
                    "INSERT INTO source_endpoints"
                    "(id,source_id,url,access_method,connector_key,enabled,metadata) "
                    "VALUES (?,?,?,?,?,0,'{}') ON CONFLICT(id) DO UPDATE SET url=excluded.url,"
                    "access_method=excluded.access_method,connector_key=excluded.connector_key",
                    (
                        endpoint_id,
                        identity,
                        change.endpoint_url,
                        change.access_method,
                        change.connector_key or None,
                    ),
                )
            db.execute(
                "INSERT INTO maintenance_audit(kind,entity_id,action,detail,created_at) VALUES (?,?,?,?,?)",
                ("source", identity, "update" if old else "create", dump(change.model_dump()), now),
            )
        return next(s for s in self.list_sources(identity) if s.id == identity)
