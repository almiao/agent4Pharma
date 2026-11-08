"""独立人工定稿记录；旧渠道包不回填批准。"""

MIGRATION_11 = """
BEGIN IMMEDIATE;
CREATE TABLE human_package_approvals (
 id TEXT PRIMARY KEY, package_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
 payload TEXT NOT NULL, UNIQUE(package_id, fingerprint)
);
INSERT INTO schema_version(version) VALUES (11);
COMMIT;
"""
