"""工作台 schema v4：人工样本、策略版本、调查证据与变更审计。"""

MIGRATION_4 = """
BEGIN IMMEDIATE;
ALTER TABLE sources ADD COLUMN management_revision INTEGER NOT NULL DEFAULT 0;
CREATE TABLE screening_policy_versions(
 id TEXT PRIMARY KEY, version INTEGER NOT NULL UNIQUE, status TEXT NOT NULL,
 definition TEXT NOT NULL, content_hash TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX one_published_screening_policy ON screening_policy_versions(status)
 WHERE status='published';
CREATE TABLE validation_cases(
 id TEXT PRIMARY KEY, input_json TEXT NOT NULL, revision INTEGER NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE maintenance_runs(
 job_id TEXT PRIMARY KEY REFERENCES jobs(id), case_id TEXT NOT NULL REFERENCES validation_cases(id),
 policy_id TEXT NOT NULL REFERENCES screening_policy_versions(id), created_at TEXT NOT NULL);
CREATE INDEX maintenance_case_runs ON maintenance_runs(case_id,created_at);
CREATE TABLE investigation_evidence(
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), requested_url TEXT NOT NULL,
 payload TEXT NOT NULL, UNIQUE(job_id,requested_url));
CREATE TABLE maintenance_audit(
 id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, entity_id TEXT NOT NULL,
 action TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
INSERT INTO schema_version(version) VALUES (4);
COMMIT;
"""
