-- Things the village found outside itself that it cannot trade.
--
-- The signal board holds readings: a symbol the village trades, a direction
-- and a size, stamped with a bar and silent after it. Most of what the outside
-- world offers is not that. A coin launched on Pump.fun an hour ago, a trading
-- repository trending on GitHub, a model on Hugging Face, a video: none of them
-- is a symbol any firm can buy, and forcing them onto the board would mean
-- inventing a direction for them.
--
-- So they are kept here, once per thing (`source`, `item_key`), with when the
-- village first and last saw them. Nothing reads this table to decide a trade.
-- It is research: shown on Mission Control, and there for whoever — a person or
-- a later desk — wants to ask whether any of it was worth anything.

CREATE TABLE IF NOT EXISTS intel (
    id SERIAL PRIMARY KEY,
    source VARCHAR(60) NOT NULL,
    item_key VARCHAR(255) NOT NULL,
    title TEXT DEFAULT '',
    url TEXT DEFAULT '',
    symbols TEXT DEFAULT '',
    score NUMERIC,
    detail TEXT DEFAULT '{}',
    first_seen TEXT DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    last_seen TEXT DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    UNIQUE (source, item_key)
);

CREATE INDEX IF NOT EXISTS intel_source_seen ON intel (source, last_seen);

-- The first deploy declared `score DECIMAL(12,4)`, whose ceiling is
-- 99,999,999.9999, and Pump.fun's largest launches are worth more than that:
-- every one of them failed to insert. `score` holds star counts and market
-- caps, not money the village owns, so it has no business being bounded.
-- Idempotent: altering a column to the type it already has changes nothing.
ALTER TABLE intel ALTER COLUMN score TYPE NUMERIC;
