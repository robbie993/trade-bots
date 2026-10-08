-- See ../032_inbox.sql.

CREATE TABLE IF NOT EXISTS inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    platform TEXT DEFAULT '',
    url TEXT DEFAULT '',
    filename TEXT DEFAULT '',
    content_type TEXT DEFAULT '',
    size_bytes INTEGER DEFAULT 0,
    body BLOB,
    note TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER DEFAULT 0,
    title TEXT DEFAULT '',
    text TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    calls TEXT DEFAULT '[]',
    heard_by TEXT DEFAULT '',
    error TEXT DEFAULT '',
    submitted_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    claimed_at TEXT,
    read_at TEXT
);

CREATE INDEX IF NOT EXISTS inbox_status ON inbox (status, id);
