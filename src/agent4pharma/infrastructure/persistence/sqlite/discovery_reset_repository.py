"""显式重置运行数据时仅保留目录和规则，生成可验证的新库，不原地删除历史。"""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.migrations import migrate

CONFIG_TABLES = (
    "organizations",
    "venues",
    "venue_aliases",
    "conference_editions",
    "journal_metric_observations",
    "venue_evidence",
    "sources",
    "source_access_configs",
    "source_endpoints",
    "source_collection_plans",
    "source_collection_plan_versions",
    "screening_policy_versions",
    "intelligence_policies",
)


@dataclass(frozen=True)
class PreservedTable:
    """新库中明确保留的配置表及记录数，不含论文或模型结果。"""

    name: str
    rows: int


@dataclass(frozen=True)
class DiscoveryResetRepository:
    """从已备份的旧库生成配置新库；切换及文件归档由显式维护入口执行。"""

    snapshot: Path

    def build_clean_database(self, destination: Path) -> tuple[PreservedTable, ...]:
        """拒绝覆盖现存库，仅复制白名单配置；清除额度游标，验证外键和空运行表。

        不读取环境或凭证，不修改旧库，不复制资料、批次、判断、缓存、制作及调查数据。
        destination 必须是不存在的文件，失败不影响当前运行库。
        """
        if destination.exists():
            raise FileExistsError(destination)
        snapshot = self.snapshot.resolve(strict=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(":memory:", uri=True)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            migrate(db)
            db.execute("ATTACH DATABASE ? AS original", (snapshot.as_uri() + "?mode=ro",))
            preserved = []
            with db:
                db.execute("PRAGMA defer_foreign_keys=ON")
                for table in CONFIG_TABLES:
                    columns = [r[1] for r in db.execute(f'PRAGMA original.table_info("{table}")')]
                    names = ",".join('"' + name + '"' for name in columns)
                    db.execute(f'INSERT INTO main."{table}" ({names}) SELECT {names} FROM original."{table}"')
                    count = db.execute(f'SELECT count(*) FROM main."{table}"').fetchone()[0]
                    preserved.append(PreservedTable(table, count))
                db.execute("UPDATE source_access_configs SET next_request_at=0")
                for (table,) in db.execute(
                    "SELECT name FROM main.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall():
                    if table not in {*CONFIG_TABLES, "schema_version"}:
                        if db.execute(f'SELECT count(*) FROM main."{table}"').fetchone()[0]:
                            raise ValueError("新库存在非配置数据，停止重置")
                if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValueError("新库配置引用不完整，停止重置")
            db.execute("DETACH DATABASE original")
            with closing(sqlite3.connect(destination)) as output:
                db.backup(output)
                if output.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("新库完整性检查失败")
        return tuple(preserved)
