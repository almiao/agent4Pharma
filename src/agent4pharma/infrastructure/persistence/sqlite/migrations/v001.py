"""历史 schema v1：每日发现基础表；迁移语义不可就地修改。"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS sources(
 id TEXT PRIMARY KEY, descriptor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_access_configs(
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL, config TEXT NOT NULL,
 next_request_at REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS materials(
 id TEXT PRIMARY KEY, identity TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
 identifiers TEXT NOT NULL, first_seen_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_records(
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL, material_id TEXT NOT NULL REFERENCES materials(id),
 external_id TEXT NOT NULL, version_key TEXT NOT NULL, metadata TEXT NOT NULL,
 observed_at TEXT NOT NULL, UNIQUE(source_id,external_id,version_key));
CREATE TABLE IF NOT EXISTS artifacts(
 id TEXT PRIMARY KEY, material_id TEXT REFERENCES materials(id), source_record_id TEXT,
 kind TEXT NOT NULL, storage_uri TEXT NOT NULL, hash TEXT NOT NULL, provenance TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, logical_key TEXT NOT NULL UNIQUE,
 parent_id TEXT, attempt INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
 request TEXT NOT NULL, result TEXT, error TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collection_tasks(
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL, scope TEXT NOT NULL,
 checkpoint TEXT, last_success_at TEXT);
CREATE TABLE IF NOT EXISTS discovery_batches(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES jobs(id), policy_ref TEXT NOT NULL,
 requested_window TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, report_ref TEXT);
CREATE TABLE IF NOT EXISTS discovery_batch_items(
 id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL REFERENCES discovery_batches(id),
 source_record_id TEXT NOT NULL REFERENCES source_records(id), material_id TEXT NOT NULL,
 kind TEXT NOT NULL, state TEXT NOT NULL, assessment TEXT, assessment_revision INTEGER NOT NULL DEFAULT 0,
 UNIQUE(batch_id,source_record_id));
CREATE INDEX IF NOT EXISTS batch_pending ON discovery_batch_items(batch_id,state,id);
"""
