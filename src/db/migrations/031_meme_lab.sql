-- The Pump.fun lab: every trending launch the meme radar logged, bought on
-- paper. See the SQLite mirror and src/trading/sandbox/memes.py for the
-- reasoning.
--
-- Short version: the radar has logged well over a thousand Pump.fun and
-- DexScreener tokens to `intel` and nothing ever asked what buying them would
-- have done. The lab buys each one on paper the first time it shows up, at
-- DexScreener's price, and holds it an hour, a day and a week.
--
-- Its own table, written only through the sandbox writer. None of these
-- tokens can be bought on the village's broker, and nothing here is a fill.
CREATE TABLE IF NOT EXISTS meme_lab (
    id SERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    token_key TEXT NOT NULL,
    chain TEXT NOT NULL,
    address TEXT NOT NULL,
    name TEXT DEFAULT '',
    horizon TEXT NOT NULL DEFAULT '',
    seen_at TEXT,
    opened_at TEXT NOT NULL,
    due_at TEXT,
    entry_price NUMERIC,
    entry_liquidity NUMERIC,
    entry_mcap NUMERIC,
    exit_price NUMERIC,
    exit_liquidity NUMERIC,
    return_pct NUMERIC,
    status TEXT NOT NULL,
    closed_at TEXT,
    created_at TEXT DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
);

CREATE INDEX IF NOT EXISTS idx_meme_lab_due ON meme_lab (status, due_at);
CREATE INDEX IF NOT EXISTS idx_meme_lab_token ON meme_lab (token_key);
CREATE INDEX IF NOT EXISTS idx_meme_lab_score ON meme_lab (source, horizon, return_pct);
