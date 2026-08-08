-- SQLite mirror of 006. See the Postgres file for why the JSON columns are
-- TEXT in both dialects.
CREATE TABLE IF NOT EXISTS file_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name TEXT,
    file_path TEXT,
    file_hash TEXT,
    verdict TEXT,
    risk_score NUMERIC,
    summary TEXT,
    backends TEXT,
    evidence TEXT,
    investigation TEXT,
    trial_transcript TEXT,
    reviewed_at TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_file_cases_verdict ON file_cases(verdict);
CREATE INDEX IF NOT EXISTS idx_file_cases_hash ON file_cases(file_hash);
