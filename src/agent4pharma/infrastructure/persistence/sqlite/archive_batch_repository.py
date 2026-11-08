"""已归档资料汇批工作单元：离线复制证据，创建新成员，不移动或改写历史批次。"""

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction
from agent4pharma.runtime.settings import ApplicationSettings
from agent4pharma.shared.clock import utcnow
from agent4pharma.shared.serialization import dump


@dataclass(frozen=True)
class ArchiveBatchResult:
    """汇批后的实际成员数；来源完成状态仅表示本地归档导入完成。"""

    batch_id: str
    records: int
    materials: int
    sources: int


@dataclass
class ArchiveBatchRepository:
    """调用者持有 worker_lock；数据库原子提交，文件为可重复写入的内容寻址副本。"""

    root: Path

    def backup(self, target: Path) -> None:
        """使用 SQLite 在线备份 API 保存一致快照，拒绝覆盖已有备份。"""
        if target.exists():
            raise FileExistsError(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.root / "discovery.sqlite3")) as origin:
            with closing(sqlite3.connect(target)) as destination:
                origin.backup(destination)

    def latest_config(self) -> ApplicationSettings:
        """读取最新发现运行的非凭证配置；不读取环境或密钥。"""
        with transaction(self.root) as db:
            row = db.execute(
                "SELECT j.request FROM discovery_batches b JOIN jobs j ON j.id=b.run_id "
                "ORDER BY b.rowid DESC LIMIT 1"
            ).fetchone()
        if row is None:
            raise ValueError("没有可复用的运行配置")
        return ApplicationSettings.model_validate({**json.loads(row[0])["policy"], "data_dir": self.root})

    def append_archives(self, active_batch: str, archives: list[Path]) -> int:
        """显式向活动归档批次补入未出现过的来源文章，不替换已有成员或判断。

        用于删除旧验收库前保全独有资料；新增成员待粗筛。复制文件并校验 hash，
        数据库出错原子回滚；操作须在 worker_lock 内并先备份。
        """
        with transaction(self.root) as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT kind FROM jobs WHERE id=?", (active_batch,)).fetchone()
            if job is None or job[0] != "archive_import":
                raise ValueError("只能向活动归档批次补入资料")
            for root in archives:
                self._import_archive(db, root.resolve())
            cursor = db.execute(
                "INSERT INTO discovery_batch_items(batch_id,source_record_id,material_id,kind,state) "
                "SELECT ?,r.id,r.material_id,'archive_rescue','pending' FROM source_records r "
                "WHERE NOT EXISTS (SELECT 1 FROM source_records newer WHERE newer.source_id=r.source_id "
                "AND newer.external_id=r.external_id AND (newer.observed_at>r.observed_at OR "
                "(newer.observed_at=r.observed_at AND newer.rowid>r.rowid))) "
                "AND NOT EXISTS (SELECT 1 FROM discovery_batch_items i JOIN source_records old ON "
                "old.id=i.source_record_id WHERE i.batch_id=? AND old.source_id=r.source_id "
                "AND old.external_id=r.external_id)",
                (active_batch, active_batch),
            )
            return cursor.rowcount

    def consolidate(
        self, batch_id: str, archives: list[Path], config: ApplicationSettings
    ) -> ArchiveBatchResult:
        """导入指定归档库并按来源/外部 ID 取最新版本，包括主库 demo。

        不导入模型结论、采集游标或旧批次；不联网。重用 batch_id 时只返回原快照，
        不追加新成员；配置或归档清单不同则拒绝。文件缺失/哈希错误导致数据库回滚。
        外部库的机构/载体关系必须已存在于目标目录，否则外键校验拒绝导入。
        """
        roots = sorted({p.resolve() for p in archives} - {self.root.resolve()})
        request = {
            "policy": config.snapshot(),
            "archive_roots": [str(p) for p in roots],
            "mode": "archive-consolidation-v1",
            "selection": "latest-per-source-external-id",
        }
        with transaction(self.root) as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT request FROM jobs WHERE id=?", (batch_id,)).fetchone()
            if old:
                if json.loads(old[0]) != request:
                    raise ValueError("批次 ID 已绑定不同归档清单或策略")
                return self._result(db, batch_id)
            for root in roots:
                self._import_archive(db, root)
            members = db.execute(
                "SELECT * FROM (SELECT r.*,row_number() OVER (PARTITION BY source_id,external_id "
                "ORDER BY observed_at DESC,rowid DESC) AS rank FROM source_records r) WHERE rank=1 "
                "ORDER BY source_id,external_id"
            ).fetchall()
            source_ids = sorted({r["source_id"] for r in members})
            now = utcnow()
            db.execute(
                "INSERT INTO jobs(id,kind,logical_key,parent_id,attempt,status,request,updated_at) "
                "VALUES (?,'archive_import',?,NULL,0,'partial',?,?)",
                (batch_id, batch_id, dump(request), now),
            )
            db.execute(
                "INSERT INTO discovery_batches(id,run_id,policy_ref,requested_window) VALUES (?,?,?,?)",
                (
                    batch_id,
                    batch_id,
                    config.policy_version,
                    dump({"mode": "archived_records", "imported_at": now, "timezone": config.timezone}),
                ),
            )
            for record in members:
                db.execute(
                    "INSERT INTO discovery_batch_items(batch_id,source_record_id,material_id,kind,state) "
                    "VALUES (?,?,?,'archive_import','pending')",
                    (batch_id, record["id"], record["material_id"]),
                )
            for source in source_ids:
                count = sum(r["source_id"] == source for r in members)
                job_id = f"{batch_id}:{source}"
                db.execute(
                    "INSERT INTO jobs(id,kind,logical_key,parent_id,attempt,status,request,result,"
                    "updated_at) "
                    "VALUES (?,'collect',?,?,0,'complete',?,?,?)",
                    (
                        job_id,
                        job_id,
                        batch_id,
                        dump({"source": source, "mode": "archive_import"}),
                        dump({"total": count, "date_basis": "archived snapshot; coverage not revalidated"}),
                        now,
                    ),
                )
            return self._result(db, batch_id)

    def _import_archive(self, db: sqlite3.Connection, root: Path) -> None:
        with closing(sqlite3.connect((root / "discovery.sqlite3").as_uri() + "?mode=ro", uri=True)) as origin:
            origin.row_factory = sqlite3.Row
            origin.execute("BEGIN")
            for table in ("sources", "materials", "source_records", "artifacts"):
                columns = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
                for row in origin.execute(f"SELECT * FROM {table}"):
                    payload = dict(row)
                    if table == "artifacts":
                        payload["storage_uri"] = self._copy_artifact(root, row)
                    existing = db.execute(f"SELECT * FROM {table} WHERE id=?", (row["id"],)).fetchone()
                    if existing:
                        # 可变目录和资料展示字段归主库，来源版本及证据身份则不可冲突。
                        immutable = ("source_id", "material_id", "external_id", "version_key", "metadata")
                        if table == "source_records" and any(existing[k] != row[k] for k in immutable):
                            raise ValueError("来源版本身份冲突")
                        if table == "artifacts" and existing["hash"] != row["hash"]:
                            raise ValueError("证据身份冲突")
                        continue
                    db.execute(
                        f"INSERT INTO {table}({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                        [payload[c] for c in columns],
                    )

    def _copy_artifact(self, root: Path, row: sqlite3.Row) -> str:
        source = (root / row["storage_uri"]).resolve()
        if not source.is_relative_to(root / "artifacts"):
            raise ValueError("归档证据路径越界")
        body = source.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        if digest != row["hash"]:
            raise ValueError("归档证据哈希不匹配")
        target = self.root / "artifacts" / digest[:2] / digest
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError("目标证据哈希不匹配")
        else:
            target.write_bytes(body)
        return str(target.relative_to(self.root))

    @staticmethod
    def _result(db: sqlite3.Connection, batch_id: str) -> ArchiveBatchResult:
        row = db.execute(
            "SELECT count(*),count(DISTINCT i.material_id),count(DISTINCT r.source_id) "
            "FROM discovery_batch_items i JOIN source_records r ON r.id=i.source_record_id WHERE batch_id=?",
            (batch_id,),
        ).fetchone()
        return ArchiveBatchResult(batch_id, *row)
