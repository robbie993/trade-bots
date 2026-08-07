-- SQLite mirror of 004_create_human_approvals.sql.

CREATE TABLE IF NOT EXISTS human_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id INTEGER REFERENCES experiments(id),
    action TEXT,
    requested_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    approved_at TEXT,
    approved_by TEXT,
    status TEXT DEFAULT 'pending',
    notes TEXT,

    amount NUMERIC,
    reason TEXT,
    details TEXT
);

CREATE INDEX IF NOT EXISTS idx_human_approvals_status ON human_approvals(status);
CREATE INDEX IF NOT EXISTS idx_human_approvals_experiment ON human_approvals(experiment_id);
