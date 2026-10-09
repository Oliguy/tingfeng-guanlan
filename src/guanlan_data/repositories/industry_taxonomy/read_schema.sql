CREATE TABLE IF NOT EXISTS sp_taxonomy_read_snapshots (
 id TEXT PRIMARY KEY, schema_version TEXT NOT NULL, scope_key TEXT NOT NULL,
 scope_json TEXT NOT NULL, catalog_revision INTEGER, catalog_json TEXT NOT NULL,
 input_hash TEXT NOT NULL, manifest_json TEXT NOT NULL, dependencies_json TEXT NOT NULL,
 coverage_json TEXT NOT NULL, evidence_checked_at TEXT NOT NULL, created_at TEXT NOT NULL,
 result_hash TEXT NOT NULL, state TEXT NOT NULL CHECK(state='ready')
);
CREATE INDEX IF NOT EXISTS sp_taxonomy_read_scope ON sp_taxonomy_read_snapshots(scope_key,created_at DESC);
CREATE TABLE IF NOT EXISTS sp_taxonomy_read_items (
 snapshot_id TEXT NOT NULL REFERENCES sp_taxonomy_read_snapshots(id),
 code TEXT NOT NULL, security_id INTEGER NOT NULL, company_id INTEGER NOT NULL,
 membership_id INTEGER NOT NULL REFERENCES sp_taxonomy_memberships(id),
 node_id TEXT NOT NULL, report_period TEXT NOT NULL, name TEXT NOT NULL,
 product TEXT NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL, known_at TEXT,
 summary_json TEXT NOT NULL, evidence_json TEXT NOT NULL, original_json TEXT NOT NULL,
 PRIMARY KEY(snapshot_id,code,membership_id)
);
CREATE INDEX IF NOT EXISTS sp_taxonomy_read_node ON sp_taxonomy_read_items(snapshot_id,node_id,stage,code);
CREATE INDEX IF NOT EXISTS sp_taxonomy_read_code ON sp_taxonomy_read_items(snapshot_id,code,node_id);
CREATE INDEX IF NOT EXISTS sp_taxonomy_read_filter ON sp_taxonomy_read_items(snapshot_id,stage,status,code,node_id);
CREATE INDEX IF NOT EXISTS sp_taxonomy_member_period ON sp_taxonomy_memberships(report_period,company_id,created_at,id);
CREATE INDEX IF NOT EXISTS sp_taxonomy_revision_time ON sp_taxonomy_revisions(created_at,id);
