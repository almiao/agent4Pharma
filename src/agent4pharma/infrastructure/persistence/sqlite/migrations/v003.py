"""历史 schema v3：机构、会议届次、来源入口与资料归属；后续变更使用新迁移。"""

MIGRATION_3 = """
BEGIN IMMEDIATE;
CREATE TABLE organizations(
 id TEXT PRIMARY KEY, name TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('company','university','institute','lab','media','association')),
 homepage_url TEXT, parent_id TEXT REFERENCES organizations(id),
 watch_status TEXT NOT NULL DEFAULT 'candidate', metadata TEXT NOT NULL DEFAULT '{}');
CREATE TABLE conference_editions(
 id TEXT PRIMARY KEY, venue_id TEXT NOT NULL REFERENCES venues(id), year INTEGER NOT NULL,
 name TEXT NOT NULL, homepage_url TEXT, start_date TEXT, end_date TEXT,
 UNIQUE(venue_id,year,name));
ALTER TABLE venues ADD COLUMN homepage_url TEXT;
ALTER TABLE sources ADD COLUMN name TEXT;
ALTER TABLE sources ADD COLUMN source_kind TEXT;
ALTER TABLE sources ADD COLUMN organization_id TEXT REFERENCES organizations(id);
ALTER TABLE sources ADD COLUMN venue_id TEXT REFERENCES venues(id);
ALTER TABLE sources ADD COLUMN homepage_url TEXT;
ALTER TABLE sources ADD COLUMN status TEXT NOT NULL DEFAULT 'candidate';
ALTER TABLE sources ADD COLUMN platform TEXT;
ALTER TABLE sources ADD COLUMN external_id TEXT;
ALTER TABLE sources ADD COLUMN publisher_name TEXT;
CREATE TABLE source_endpoints(
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
 edition_id TEXT REFERENCES conference_editions(id), url TEXT NOT NULL,
 access_method TEXT NOT NULL CHECK(access_method IN ('api','rss','html','browser','app')),
 connector_key TEXT, access_config_id TEXT REFERENCES source_access_configs(id),
 enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
 metadata TEXT NOT NULL DEFAULT '{}', UNIQUE(source_id,url));
ALTER TABLE materials ADD COLUMN material_type TEXT NOT NULL DEFAULT 'paper';
ALTER TABLE materials ADD COLUMN canonical_url TEXT;
ALTER TABLE materials ADD COLUMN published_at TEXT;
ALTER TABLE materials ADD COLUMN publisher_organization_id TEXT REFERENCES organizations(id);
ALTER TABLE materials ADD COLUMN venue_id TEXT REFERENCES venues(id);
ALTER TABLE materials ADD COLUMN conference_edition_id TEXT REFERENCES conference_editions(id);
ALTER TABLE materials ADD COLUMN dataset TEXT NOT NULL DEFAULT 'production';
ALTER TABLE collection_tasks ADD COLUMN endpoint_id TEXT REFERENCES source_endpoints(id);
CREATE INDEX source_owner ON sources(organization_id);
CREATE INDEX material_directory ON materials(dataset,material_type,venue_id);
CREATE INDEX endpoint_source ON source_endpoints(source_id,enabled);
INSERT INTO schema_version VALUES (3);
COMMIT;
"""
