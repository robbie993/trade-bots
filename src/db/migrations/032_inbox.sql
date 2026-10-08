-- What the operator sends the village by hand: a link or a file.
--
-- The social readers find things on their own; this is the other direction, a
-- person saying "look at this". One row per thing sent. A link to Instagram,
-- TikTok or X is fetched by the village browser on the operator's PC, the only
-- place signed in to them; a video, audio clip, picture or document is kept
-- here (`body`) until the PC has watched or read it, and the bytes are then
-- dropped. Text files are read by the web service the moment they arrive.
--
-- What was heard is kept (`text`, `summary`) with the explicit calls found in
-- it (`calls`, by the same rule as the social readers). Nothing here is a
-- reading on the signal board: no firm hears it. The calls are tested on paper
-- in the idea lab as one more publisher, `sent_by_you`, and nothing else.

CREATE TABLE IF NOT EXISTS inbox (
    id SERIAL PRIMARY KEY,
    kind VARCHAR(20) NOT NULL,
    platform VARCHAR(40) DEFAULT '',
    url TEXT DEFAULT '',
    filename TEXT DEFAULT '',
    content_type TEXT DEFAULT '',
    size_bytes INTEGER DEFAULT 0,
    body BYTEA,
    note TEXT DEFAULT '',
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    attempts INTEGER DEFAULT 0,
    title TEXT DEFAULT '',
    text TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    calls TEXT DEFAULT '[]',
    heard_by TEXT DEFAULT '',
    error TEXT DEFAULT '',
    submitted_at TEXT DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    claimed_at TEXT,
    read_at TEXT
);

CREATE INDEX IF NOT EXISTS inbox_status ON inbox (status, id);
