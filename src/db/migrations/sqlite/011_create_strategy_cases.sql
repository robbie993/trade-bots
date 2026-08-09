CREATE TABLE IF NOT EXISTS strategy_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_id INTEGER REFERENCES firms(id),
    file_path TEXT,
    file_name TEXT,
    file_sha256 TEXT,
    file_bytes INTEGER DEFAULT 0,
    submitted_by TEXT,
    genome TEXT,
    universe TEXT,
    evidence TEXT,
    findings TEXT,
    prosecution TEXT,
    defence TEXT,
    ruling TEXT,
    ruling_reason TEXT,
    confidence NUMERIC DEFAULT 0,
    fitness NUMERIC,
    genome_id INTEGER,
    status TEXT DEFAULT 'ruled',
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_strategy_cases_ruling ON strategy_cases(ruling);
CREATE INDEX IF NOT EXISTS idx_strategy_cases_sha ON strategy_cases(file_sha256);
