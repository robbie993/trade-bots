CREATE TABLE IF NOT EXISTS council_rulings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    approval_id INTEGER REFERENCES human_approvals(id),
    action TEXT NOT NULL,
    firm_key TEXT,
    verdict TEXT NOT NULL,
    reason TEXT,
    confidence NUMERIC DEFAULT 0,
    for_weight NUMERIC DEFAULT 0,
    against_weight NUMERIC DEFAULT 0,
    findings TEXT,
    evidence TEXT,
    evidence_digest TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_council_rulings_firm ON council_rulings(firm_key, action);
CREATE INDEX IF NOT EXISTS idx_council_rulings_approval ON council_rulings(approval_id);
