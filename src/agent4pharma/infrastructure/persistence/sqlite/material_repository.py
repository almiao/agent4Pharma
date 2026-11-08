"""SQLite 资料及产物存取：维护稳定身份、来源版本和原始文件的内容寻址引用。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from agent4pharma.modules.materials.collection_records import (
    CollectedRecord,
    PersistedRecord,
    SourceRecordReading,
    SourceRecordSnapshot,
)
from agent4pharma.modules.materials.material_identity import identity as material_identity
from agent4pharma.shared.clock import utcnow
from agent4pharma.shared.serialization import dump


@dataclass
class MaterialRepository:
    """资料/产物仓储；页内写入参与外层事务，不自行提交。"""

    root: Path
    db: sqlite3.Connection

    def source_record_snapshot(self, record_id: str) -> SourceRecordSnapshot:
        """读取资料模块拥有的固定来源记录，供其他用例通过公开契约消费。"""
        row = self.db.execute(
            "SELECT source_id,metadata FROM source_records WHERE id=?", (record_id,)
        ).fetchone()
        if row is None:
            raise KeyError("来源记录不存在")
        return self._source_snapshot(record_id, row)

    def source_record_snapshots(self, record_ids: list[str]) -> dict[str, SourceRecordSnapshot]:
        """同一工作单元批量读取准确来源版本；缺失仍报错，不返回不完整的成功结果。"""
        identities = list(dict.fromkeys(record_ids))
        snapshots = {}
        for start in range(0, len(identities), 500):
            batch = identities[start : start + 500]
            placeholders = ",".join("?" for _ in batch)
            for row in self.db.execute(
                f"SELECT id,source_id,metadata FROM source_records WHERE id IN ({placeholders})", batch
            ):
                snapshots[row["id"]] = self._source_snapshot(row["id"], row)
        if len(snapshots) != len(identities):
            raise KeyError("来源记录不存在")
        return snapshots

    @staticmethod
    def _source_snapshot(record_id: str, row: sqlite3.Row) -> SourceRecordSnapshot:
        data = json.loads(row["metadata"])
        return SourceRecordSnapshot(
            source_record_id=record_id,
            source_id=row["source_id"],
            title=data["title"],
            abstract=data.get("abstract", ""),
            url=data.get("url", ""),
            metadata=data if data.get("dataset") == "demo" else data.get("metadata", {}),
            identifiers=data.get("identifiers", {}),
        )

    def source_record_reading(self, record_id: str, offset: int = 0) -> SourceRecordReading:
        """读取固定来源版本正文，每页最多 20000 字；缺失文件明确返回不可读，不联网。"""
        if offset < 0:
            raise ValueError("正文偏移不能为负数")
        snapshot = self.source_record_snapshot(record_id)
        raw = json.loads(
            self.db.execute("SELECT metadata FROM source_records WHERE id=?", (record_id,)).fetchone()[0]
        )
        reading = SourceRecordReading(
            source_record_id=record_id,
            title=snapshot.title,
            url=snapshot.url,
            published=raw.get("published") or snapshot.metadata.get("published_raw"),
            abstract=snapshot.abstract,
            title_is_provisional=str(snapshot.metadata.get("classification_state", "")).startswith(
                "provisional"
            )
            or "title inferred from URL" in snapshot.title,
            availability="restricted"
            if snapshot.metadata.get("access_state") == "http_403"
            else "abstract_only"
            if snapshot.abstract
            else "link_only",
        )
        artifact = self.db.execute(
            "SELECT id,kind FROM artifacts WHERE source_record_id=? "
            "AND kind IN ('fulltext','preview','truncated') "
            "ORDER BY CASE kind WHEN 'fulltext' THEN 0 ELSE 1 END,rowid DESC LIMIT 1",
            (record_id,),
        ).fetchone()
        if artifact:
            try:
                content = self.read_artifact(artifact["id"], offset, 20000)
            except FileNotFoundError:
                reading.availability, reading.read_error = "unavailable", "已归档正文文件缺失"
                return reading
            reading.body, reading.total_chars = content["text"], content["total_chars"]
            reading.availability = "fulltext" if artifact["kind"] == "fulltext" else "partial"
        else:
            content = raw.get("content") or {}
            text = content.get("text", "")
            if text:
                reading.body, reading.total_chars = text[offset : offset + 20000], len(text)
                reading.availability = "fulltext" if content.get("access_state") == "fulltext" else "partial"
        if reading.total_chars > offset + len(reading.body):
            reading.next_offset = offset + len(reading.body)
        return reading

    def artifact_material(self, artifact_id: str) -> str | None:
        """返回产物关联的资料 ID；产物缺失或未关联资料时返回 None。"""
        row = self.db.execute("SELECT material_id FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        return row[0] if row else None

    def put_artifact(
        self,
        body: bytes,
        kind: str,
        provenance: dict,
        material_id: str | None = None,
        record_id: str | None = None,
    ) -> str:
        """按内容哈希保存文件并登记出处；参与调用方事务，不自行提交数据库。"""
        digest = hashlib.sha256(body).hexdigest()
        directory = self.root / "artifacts" / digest[:2]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / digest
        if not path.exists():
            temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            temporary.write_bytes(body)
            temporary.replace(path)
        artifact_id = hashlib.sha256(
            f"{digest}:{kind}:{record_id or ''}:{dump(provenance)}".encode()
        ).hexdigest()
        self.db.execute(
            "INSERT OR IGNORE INTO artifacts VALUES (?,?,?,?,?,?,?)",
            (
                artifact_id,
                material_id,
                record_id,
                kind,
                str(path.relative_to(self.root)),
                digest,
                dump(provenance),
            ),
        )
        return artifact_id

    def material(self, material_id: str) -> dict:
        """返回资料、来源版本和产物引用；不存在时抛出 KeyError，访问范围由上层服务校验。"""
        row = self.db.execute("SELECT * FROM materials WHERE id=?", (material_id,)).fetchone()
        if row is None:
            raise KeyError(material_id)
        result = dict(row)
        result["identifiers"] = json.loads(result["identifiers"])
        result["records"] = [
            dict(r)
            for r in self.db.execute(
                "SELECT id,source_id,metadata FROM source_records WHERE material_id=?", (material_id,)
            )
        ]
        for record in result["records"]:
            record["metadata"] = json.loads(record["metadata"])
        result["artifacts"] = [
            dict(r)
            for r in self.db.execute(
                "SELECT id,kind,source_record_id FROM artifacts WHERE material_id=?", (material_id,)
            )
        ]
        return result

    def read_artifact(self, artifact_id: str, offset: int = 0, length: int = 12000) -> dict:
        """按字符偏移读取产物片段及出处；拒绝非法长度、负偏移和越界存储路径。"""
        if offset < 0 or not 1 <= length <= 20000:
            raise ValueError("offset >= 0 and length 1..20000 required")
        row = self.db.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if row is None:
            raise KeyError(artifact_id)
        path = (self.root / row["storage_uri"]).resolve()
        if not path.is_relative_to(self.root / "artifacts"):
            raise ValueError("Invalid artifact path")
        text = path.read_text(encoding="utf-8")
        return {
            "text": text[offset : offset + length],
            "offset": offset,
            "total_chars": len(text),
            "truncated": offset + length < len(text),
            "provenance": json.loads(row["provenance"]),
        }

    def ingest_record(self, record: CollectedRecord, raw_refs: list[str]) -> PersistedRecord:
        """Participates in the caller's page transaction; never commits here."""
        identity = material_identity(record.source, record.external_id, record.identifiers)
        material_id = hashlib.sha256(identity.encode()).hexdigest()
        existed = self.db.execute("SELECT 1 FROM materials WHERE id=?", (material_id,)).fetchone()
        self.db.execute(
            "INSERT OR IGNORE INTO materials(id,identity,title,identifiers,first_seen_at) VALUES (?,?,?,?,?)",
            (
                material_id,
                identity,
                record.title,
                dump(record.identifiers),
                utcnow(),
            ),
        )
        payload = asdict(record)
        if record.content is None:
            # 旧连接器不因新增可选字段改变历史版本身份。
            payload.pop("content")
        version = record.version or hashlib.sha256(dump(payload).encode()).hexdigest()[:20]
        record_id = hashlib.sha256(f"{record.source}:{record.external_id}:{version}".encode()).hexdigest()
        known = self.db.execute("SELECT 1 FROM source_records WHERE id=?", (record_id,)).fetchone()
        self.db.execute(
            "INSERT OR IGNORE INTO source_records VALUES (?,?,?,?,?,?,?)",
            (
                record_id,
                record.source,
                material_id,
                record.external_id,
                version,
                dump(payload),
                utcnow(),
            ),
        )
        if record.content is not None:
            self.db.execute(
                "UPDATE materials SET title=?,canonical_url=?,published_at=?,material_type=? WHERE id=?",
                (record.title, record.url, record.published, record.metadata["material_type"], material_id),
            )
            self.put_artifact(
                record.content.text.encode(),
                record.content.access_state,
                {"raw_refs": raw_refs, "url": record.url, "parser": record.content.parser_version},
                material_id,
                record_id,
            )
        if record.abstract:
            self.put_artifact(
                record.abstract.encode(),
                "abstract",
                {
                    "raw_refs": raw_refs,
                    "url": record.url,
                    "parser": "connectors-v1",
                },
                material_id,
                record_id,
            )
        self.put_artifact(dump(payload).encode(), "metadata", {"raw_refs": raw_refs}, material_id, record_id)
        return PersistedRecord(material_id, record_id, bool(existed), bool(known))

    def import_demo_materials(self, seed: dict, venues: dict[str, str]) -> None:
        """保存 demo 资料与来源版本，参与目录导入的外层事务，不自行提交。"""
        for item in seed["materials"]:
            identity = "demo:url:" + item["url"]
            mid = hashlib.sha256(identity.encode()).hexdigest()
            self.db.execute(
                "INSERT INTO materials(id,identity,title,identifiers,first_seen_at,material_type,"
                "canonical_url,published_at,publisher_organization_id,venue_id,"
                "conference_edition_id,dataset) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,'demo') ON CONFLICT(identity) DO UPDATE SET "
                "title=excluded.title,material_type=excluded.material_type,canonical_url=excluded.canonical_url,"
                "published_at=excluded.published_at,publisher_organization_id=excluded.publisher_organization_id,"
                "venue_id=excluded.venue_id,conference_edition_id=excluded.conference_edition_id",
                (
                    mid,
                    identity,
                    item["title"],
                    dump(item.get("identifiers", {})),
                    utcnow(),
                    item["material_type"],
                    item["url"],
                    item.get("published_at"),
                    item.get("organization_id"),
                    venues.get(item.get("venue_key")),
                    item.get("edition_id"),
                ),
            )
            metadata = {**item, "dataset": "demo", "seed_version": seed["version"]}
            version = hashlib.sha256(dump(metadata).encode()).hexdigest()
            rid = hashlib.sha256(f"{item['source_id']}:{mid}:{version}".encode()).hexdigest()
            self.db.execute(
                "INSERT OR IGNORE INTO source_records"
                "(id,source_id,material_id,external_id,version_key,metadata,observed_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (rid, item["source_id"], mid, item["url"], version, dump(metadata), utcnow()),
            )
