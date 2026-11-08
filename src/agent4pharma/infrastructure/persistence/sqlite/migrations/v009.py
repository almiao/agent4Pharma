"""本地 RSS 判别结果和批次交接账本，追加历史而不覆盖远程精筛。"""

MIGRATION_9 = """
BEGIN IMMEDIATE;
CREATE TABLE rss_triage_decisions(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE rss_triage_links(batch_id TEXT NOT NULL, source_record_id TEXT NOT NULL,
 decision_id TEXT NOT NULL REFERENCES rss_triage_decisions(id),
 PRIMARY KEY(batch_id,source_record_id));
CREATE TABLE rss_triage_errors(batch_id TEXT NOT NULL, source_record_id TEXT NOT NULL,
 error TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(batch_id,source_record_id));
INSERT INTO schema_version(version) VALUES (9);
COMMIT;
"""
