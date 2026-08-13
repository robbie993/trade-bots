-- Mirror of 019_fill_provenance.sql for SQLite. See that file for the
-- $20.4m equity on $289k of capital that made this necessary.

CREATE TABLE IF NOT EXISTS position_provenance (
    firm_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    priced_by TEXT NOT NULL,
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (firm_id, symbol)
);
