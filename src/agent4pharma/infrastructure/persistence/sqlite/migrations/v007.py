"""制作业务专用表及任务租约；既有候选、评估和历史批次不迁改。"""

MIGRATION_7 = """
BEGIN IMMEDIATE;
CREATE TABLE production_stories(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE TABLE editorial_decisions(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE commission_versions(story_id TEXT NOT NULL REFERENCES production_stories(id),
 version INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(story_id,version));
CREATE TABLE content_drafts(id TEXT PRIMARY KEY, story_id TEXT NOT NULL REFERENCES production_stories(id),
 number INTEGER NOT NULL, payload TEXT NOT NULL, UNIQUE(story_id,number));
CREATE TABLE content_evidence(story_id TEXT NOT NULL REFERENCES production_stories(id),
 evidence_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(story_id,evidence_id));
CREATE TABLE editorial_reviews(id TEXT PRIMARY KEY, story_id TEXT NOT NULL REFERENCES production_stories(id),
 draft_id TEXT NOT NULL REFERENCES content_drafts(id), payload TEXT NOT NULL);
CREATE TABLE channel_packages(id TEXT PRIMARY KEY, story_id TEXT NOT NULL REFERENCES production_stories(id),
 draft_id TEXT NOT NULL REFERENCES content_drafts(id), payload TEXT NOT NULL);
CREATE TABLE release_receipts(id TEXT PRIMARY KEY, package_id TEXT NOT NULL REFERENCES channel_packages(id),
 payload TEXT NOT NULL);
CREATE TABLE production_tasks(id TEXT PRIMARY KEY, role TEXT NOT NULL, story_id TEXT NOT NULL,
 status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL);
CREATE INDEX production_task_queue ON production_tasks(status,story_id);
CREATE TABLE production_operator_events(id TEXT PRIMARY KEY, story_id TEXT NOT NULL, actor_id TEXT NOT NULL,
 action TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
INSERT INTO schema_version(version) VALUES (7);
COMMIT;
"""
