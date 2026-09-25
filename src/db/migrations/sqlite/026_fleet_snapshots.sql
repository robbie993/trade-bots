CREATE TABLE IF NOT EXISTS fleet_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    service TEXT DEFAULT '',
    remote_path TEXT DEFAULT '',
    fetched_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS fleet_snapshots_source_id ON fleet_snapshots (source, id);
