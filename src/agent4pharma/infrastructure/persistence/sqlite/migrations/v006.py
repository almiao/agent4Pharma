"""通用采集配置及不可变定义历史；不修改旧任务和 demo 资料。"""

MIGRATION_6 = """
BEGIN IMMEDIATE;
CREATE TABLE source_collection_plans(
 source_id TEXT PRIMARY KEY REFERENCES sources(id), definition TEXT NOT NULL,
 content_hash TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)));
CREATE TABLE source_collection_plan_versions(
 source_id TEXT NOT NULL REFERENCES sources(id), content_hash TEXT NOT NULL,
 definition TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(source_id,content_hash));
INSERT INTO schema_version(version) VALUES (6);
COMMIT;
"""
