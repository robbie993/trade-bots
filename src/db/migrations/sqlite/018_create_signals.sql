-- Mirror of 018_create_signals.sql for SQLite. See that file for why a scanner
-- publishes a reading instead of an order, and why a stale reading is silence.

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    publisher TEXT NOT NULL,
    symbol TEXT NOT NULL,
    score NUMERIC DEFAULT 0,
    confidence NUMERIC DEFAULT 0,
    note TEXT,
    as_of TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol, id);
CREATE INDEX IF NOT EXISTS idx_signals_publisher ON signals(publisher, as_of);
