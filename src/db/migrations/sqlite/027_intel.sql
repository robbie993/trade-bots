CREATE TABLE IF NOT EXISTS intel (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    item_key TEXT NOT NULL,
    title TEXT DEFAULT '',
    url TEXT DEFAULT '',
    symbols TEXT DEFAULT '',
    score NUMERIC,
    detail TEXT DEFAULT '{}',
    first_seen TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    last_seen TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (source, item_key)
);

CREATE INDEX IF NOT EXISTS intel_source_seen ON intel (source, last_seen);
