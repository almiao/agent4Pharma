"""独立阅读品质报告；旧事实审核不补造阅读结论。"""

MIGRATION_12 = """
BEGIN IMMEDIATE;
CREATE TABLE reader_reviews (
 id TEXT PRIMARY KEY, story_id TEXT NOT NULL REFERENCES production_stories(id),
 draft_id TEXT NOT NULL REFERENCES content_drafts(id), payload TEXT NOT NULL
);
CREATE INDEX reader_reviews_story ON reader_reviews(story_id, draft_id);
INSERT INTO schema_version(version) VALUES (12);
COMMIT;
"""
