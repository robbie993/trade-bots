-- Mirror of 017_create_village_settings.sql for SQLite. See that file for why
-- these switches live in the database rather than in the environment.

CREATE TABLE IF NOT EXISTS village_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_by TEXT,
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
