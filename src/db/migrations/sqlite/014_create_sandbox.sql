CREATE TABLE IF NOT EXISTS alliances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    members TEXT,
    founder TEXT,
    pact TEXT,
    standing NUMERIC DEFAULT 100,
    status TEXT DEFAULT 'active',
    formed_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    broken_at TEXT,
    broken_by TEXT
);

CREATE TABLE IF NOT EXISTS sandbox_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season INTEGER DEFAULT 1,
    event_type TEXT,
    actor TEXT,
    target TEXT,
    alliance_id INTEGER REFERENCES alliances(id),
    detail TEXT,
    payload TEXT,
    shadow_equity NUMERIC DEFAULT 0,
    reputation_delta NUMERIC DEFAULT 0,
    real_effect INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_sandbox_events_actor ON sandbox_events(actor);
CREATE INDEX IF NOT EXISTS idx_sandbox_events_type ON sandbox_events(event_type);
