"""节点提示词版本；原情报准则继续使用既有表与身份。"""

MIGRATION_10 = """
BEGIN IMMEDIATE;
CREATE TABLE prompt_versions (
 id TEXT PRIMARY KEY, node_key TEXT NOT NULL, version INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('draft','published','archived')),
 instructions TEXT NOT NULL, examples TEXT NOT NULL, note TEXT NOT NULL,
 created_at TEXT NOT NULL, content_hash TEXT NOT NULL,
 UNIQUE(node_key, version)
);
CREATE UNIQUE INDEX prompt_one_published ON prompt_versions(node_key) WHERE status='published';
INSERT INTO schema_version(version) VALUES (10);
COMMIT;
"""
