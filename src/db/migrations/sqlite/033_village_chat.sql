-- See ../033_village_chat.sql.

CREATE TABLE IF NOT EXISTS village_chat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    reply_to INTEGER,
    status TEXT DEFAULT '',
    attempts INTEGER DEFAULT 0,
    answered_by TEXT DEFAULT '',
    error TEXT DEFAULT '',
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    claimed_at TEXT
);

CREATE INDEX IF NOT EXISTS village_chat_status ON village_chat (status, id);
