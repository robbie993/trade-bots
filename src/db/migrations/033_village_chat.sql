-- Talking to the village: the operator's messages and the village's answers.
--
-- One row per message. The operator writes on the website (`role` 'you',
-- `status` 'waiting'); Claude Code on the operator's PC answers as the village
-- (`scripts/inbox_watch.py`), with a snapshot of the ledger in front of it and
-- no tools, and writes the answer as its own row (`role` 'village',
-- `reply_to` the question). Words only: nothing here is read by a firm, the
-- gate or the brokerage, so nothing said here can move money.

CREATE TABLE IF NOT EXISTS village_chat (
    id SERIAL PRIMARY KEY,
    role VARCHAR(20) NOT NULL,
    text TEXT NOT NULL,
    reply_to INTEGER,
    status VARCHAR(20) DEFAULT '',
    attempts INTEGER DEFAULT 0,
    answered_by TEXT DEFAULT '',
    error TEXT DEFAULT '',
    created_at TEXT DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    claimed_at TEXT
);

CREATE INDEX IF NOT EXISTS village_chat_status ON village_chat (status, id);
