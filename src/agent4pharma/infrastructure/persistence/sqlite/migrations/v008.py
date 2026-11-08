"""追加单篇回归账本与步骤观测，保留全部旧批次和制作版本。"""

MIGRATION_8 = """
BEGIN IMMEDIATE;
CREATE TABLE article_regressions(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE workflow_observations(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
 subject_ref TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX workflow_observation_subject ON workflow_observations(subject_ref,seq);
INSERT INTO schema_version(version) VALUES (8);
COMMIT;
"""
