"""公众号草稿交付记录；保留每次请求和未知结果，不改历史批准。"""

MIGRATION_13 = """
BEGIN IMMEDIATE;
CREATE TABLE wechat_deliveries (
    id TEXT PRIMARY KEY,
    package_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX wechat_deliveries_package ON wechat_deliveries(package_id, account_id);
INSERT INTO schema_version(version) VALUES (13);
COMMIT;
"""
