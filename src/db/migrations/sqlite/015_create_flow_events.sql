CREATE TABLE IF NOT EXISTS flow_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    node TEXT,
    edge TEXT,
    kind TEXT DEFAULT 'ok',
    label TEXT,
    firm TEXT,
    detail TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_flow_events_run ON flow_events(run_id);
