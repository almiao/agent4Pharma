"""Ordered historical migrations; preserve already applied schema semantics."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.migrations.v001 import SCHEMA
from agent4pharma.infrastructure.persistence.sqlite.migrations.v002 import MIGRATION_2
from agent4pharma.infrastructure.persistence.sqlite.migrations.v003 import MIGRATION_3
from agent4pharma.infrastructure.persistence.sqlite.migrations.v004 import MIGRATION_4
from agent4pharma.infrastructure.persistence.sqlite.migrations.v005 import MIGRATION_5
from agent4pharma.infrastructure.persistence.sqlite.migrations.v006 import MIGRATION_6
from agent4pharma.infrastructure.persistence.sqlite.migrations.v007 import MIGRATION_7
from agent4pharma.infrastructure.persistence.sqlite.migrations.v008 import MIGRATION_8
from agent4pharma.infrastructure.persistence.sqlite.migrations.v009 import MIGRATION_9
from agent4pharma.infrastructure.persistence.sqlite.migrations.v010 import MIGRATION_10
from agent4pharma.infrastructure.persistence.sqlite.migrations.v011 import MIGRATION_11
from agent4pharma.infrastructure.persistence.sqlite.migrations.v012 import MIGRATION_12
from agent4pharma.infrastructure.persistence.sqlite.migrations.v013 import MIGRATION_13


def migrate_catalog(db: sqlite3.Connection) -> None:
    """兼容历史调用的 v2 升级入口；已升级的数据库不重复执行。"""
    if db.execute("SELECT max(version) FROM schema_version").fetchone()[0] < 2:
        db.executescript(MIGRATION_2)


def migrate_registry(db: sqlite3.Connection) -> None:
    """兼容历史调用的 v3 升级入口；需先具备基础 schema 与 v2 表。"""
    if db.execute("SELECT max(version) FROM schema_version").fetchone()[0] < 3:
        db.executescript(MIGRATION_3)


def migrate(db: sqlite3.Connection) -> None:
    """按历史顺序升级 SQLite；v4 起每次升级在线备份，失败回滚并保留备份。"""
    db.executescript(SCHEMA)
    db.execute("INSERT OR IGNORE INTO schema_version VALUES (1)")
    db.commit()
    for version, script in (
        (2, MIGRATION_2),
        (3, MIGRATION_3),
        (4, MIGRATION_4),
        (5, MIGRATION_5),
        (6, MIGRATION_6),
        (7, MIGRATION_7),
        (8, MIGRATION_8),
        (9, MIGRATION_9),
        (10, MIGRATION_10),
        (11, MIGRATION_11),
        (12, MIGRATION_12),
        (13, MIGRATION_13),
    ):
        if db.execute("SELECT max(version) FROM schema_version").fetchone()[0] < version:
            if version >= 4:
                _backup_before_workbench(db, version)
            try:
                db.executescript(script)
            except BaseException:
                db.rollback()
                raise


def _backup_before_workbench(db: sqlite3.Connection, version: int) -> None:
    database_path = db.execute("PRAGMA database_list").fetchone()[2]
    if not database_path:
        return
    root = Path(database_path).parent / "backups"
    root.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    with sqlite3.connect(root / f"discovery-pre-v{version}-{stamp}.sqlite3") as target:
        db.backup(target)
