"""两 Agent 正式评估、候选及人工确认建议；旧随机批次原样保留。"""

MIGRATION_5 = """
BEGIN IMMEDIATE;
CREATE TABLE intelligence_policies(
 id TEXT PRIMARY KEY, version INTEGER NOT NULL UNIQUE, status TEXT NOT NULL,
 definition TEXT NOT NULL, content_hash TEXT NOT NULL);
CREATE UNIQUE INDEX one_intelligence_policy ON intelligence_policies(status) WHERE status='published';
CREATE TABLE intelligence_tasks(
 id TEXT PRIMARY KEY, status TEXT NOT NULL, request TEXT NOT NULL, result TEXT,
 error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
CREATE TABLE editorial_candidates(
 id TEXT PRIMARY KEY, material_ref TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES intelligence_tasks(id),
 policy_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'recommend',
 card TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(material_ref,policy_id));
CREATE TABLE intelligence_readings(
 task_id TEXT NOT NULL REFERENCES intelligence_tasks(id), url TEXT NOT NULL,
 payload TEXT NOT NULL, PRIMARY KEY(task_id,url));
CREATE TABLE intelligence_maintenance_requests(
 task_id TEXT PRIMARY KEY REFERENCES intelligence_tasks(id), reason TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE maintenance_proposals(
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES maintenance_runs(job_id),
 status TEXT NOT NULL, payload TEXT NOT NULL, resolution TEXT, created_at TEXT NOT NULL);
INSERT INTO schema_version(version) VALUES (5);
COMMIT;
"""
