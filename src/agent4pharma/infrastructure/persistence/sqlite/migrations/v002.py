"""历史 schema v2：载体、别名、指标与出处；后续变更使用新迁移。"""

MIGRATION_2 = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS venues(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, normalized_name TEXT NOT NULL UNIQUE,
 kind TEXT NOT NULL CHECK(kind IN ('journal','conference','preprint_server','repository','unknown')),
 watch_status TEXT NOT NULL CHECK(watch_status IN ('watch','candidate','unreviewed')),
 scope_note TEXT NOT NULL DEFAULT '', identifiers TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS venue_aliases(
 normalized_alias TEXT PRIMARY KEY, venue_id TEXT NOT NULL REFERENCES venues(id),
 display_alias TEXT NOT NULL, basis TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS journal_metric_observations(
 id TEXT PRIMARY KEY, venue_id TEXT NOT NULL REFERENCES venues(id), metric_type TEXT NOT NULL,
 value REAL NOT NULL, metric_year INTEGER, source_url TEXT NOT NULL, observed_at TEXT NOT NULL,
 verification_state TEXT NOT NULL CHECK(verification_state IN ('official_value','year_pending')));
CREATE TABLE IF NOT EXISTS venue_evidence(
 id TEXT PRIMARY KEY, venue_id TEXT NOT NULL REFERENCES venues(id), source_kind TEXT NOT NULL,
 source_url TEXT NOT NULL, snapshot_ref TEXT NOT NULL, record_count INTEGER,
 payload TEXT NOT NULL, observed_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS venue_evidence_lookup ON venue_evidence(venue_id,source_kind);
INSERT OR IGNORE INTO schema_version VALUES (2);
COMMIT;
"""
