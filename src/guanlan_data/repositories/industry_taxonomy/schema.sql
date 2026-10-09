CREATE TABLE IF NOT EXISTS sp_taxonomy_revisions (
 id INTEGER PRIMARY KEY, prior_id INTEGER REFERENCES sp_taxonomy_revisions(id),
 reason TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
 content_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sp_taxonomy_nodes (
 revision_id INTEGER NOT NULL REFERENCES sp_taxonomy_revisions(id),
 node_id TEXT NOT NULL, parent_id TEXT, name TEXT NOT NULL,
 position INTEGER NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
 aliases_json TEXT NOT NULL, rule_json TEXT NOT NULL, rule_hash TEXT NOT NULL,
 PRIMARY KEY(revision_id,node_id),
 FOREIGN KEY(revision_id,parent_id) REFERENCES sp_taxonomy_nodes(revision_id,node_id)
 DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE IF NOT EXISTS sp_taxonomy_runs (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, created_at TEXT NOT NULL,
 request_hash TEXT NOT NULL, input_hash TEXT NOT NULL, payload_hash TEXT NOT NULL,
 payload_json TEXT NOT NULL, receipt_json TEXT, elapsed_ms REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sp_taxonomy_memberships (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES sp_companies(id),
 node_id TEXT NOT NULL, catalog_revision INTEGER NOT NULL,
 report_period TEXT NOT NULL, product TEXT NOT NULL, stage TEXT NOT NULL,
 status TEXT NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL,
 method TEXT NOT NULL, rule_hash TEXT NOT NULL, assignment_key TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('active','removed')),
 prior_id INTEGER UNIQUE REFERENCES sp_taxonomy_memberships(id),
 run_id TEXT NOT NULL REFERENCES sp_taxonomy_runs(id), created_at TEXT NOT NULL,
 FOREIGN KEY(catalog_revision,node_id) REFERENCES sp_taxonomy_nodes(revision_id,node_id)
);
CREATE INDEX IF NOT EXISTS sp_taxonomy_member_lookup
 ON sp_taxonomy_memberships(node_id,report_period,stage,company_id);
CREATE INDEX IF NOT EXISTS sp_taxonomy_member_key ON sp_taxonomy_memberships(assignment_key,id);
CREATE TABLE IF NOT EXISTS sp_taxonomy_evidence (
 id INTEGER PRIMARY KEY, membership_id INTEGER NOT NULL REFERENCES sp_taxonomy_memberships(id),
 document_id INTEGER NOT NULL REFERENCES sp_documents(id),
 fact_id INTEGER REFERENCES sp_facts(id), evidence_id INTEGER REFERENCES sp_evidence(id),
 evidence_hash TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sp_taxonomy_evidence_member ON sp_taxonomy_evidence(membership_id);
