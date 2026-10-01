-- The Pump.fun lab: every trending launch the meme radar logged, bought on
-- paper.
--
-- The meme radar logs three lists to `intel` every bar: Pump.fun's biggest
-- coins, the ones streaming live, and DexScreener's most-boosted tokens. The
-- village cannot buy any of them, so they were kept and never asked anything.
-- Robbie wants a Pump.fun bot, and the first question such a bot lives or dies
-- by is the one this table answers: if you had bought every one of them the
-- moment it showed up, what would you have now?
--
-- **One look per token, per list.** The first time a token appears, the lab
-- prices it on DexScreener. A deep enough pool opens three rows, one per
-- horizon (`1h`, `1d`, `1w`), with `status` 'open'. A pool too thin to trade
-- is one row with `status` 'thin', and a token DexScreener has no pool for is
-- one row with `status` 'unlisted', both with an empty `horizon`: kept, so
-- that a token skipped once is never bought later once it has proven itself,
-- and so the share of launches nobody could even trade is a number too.
--
-- `return_pct` is the price move, gross, from `entry_price` to `exit_price`.
-- A pool that is gone a day after the idea fell due is `status` 'vanished' at
-- -100%: a holder of a rugged token has nothing to sell. An idea the lab could
-- not price for a day because its own requests failed is `status` 'void', with
-- no return, because an outage says nothing about the token.
--
-- **Its own table, on purpose.** Not `fills`, not `positions`, nothing that
-- touches `cash`, the same rule as `sandbox_ideas`. It is written only through
-- the sandbox writer, which refuses every other table.
CREATE TABLE IF NOT EXISTS meme_lab (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_meme_lab_due ON meme_lab (status, due_at);
CREATE INDEX IF NOT EXISTS idx_meme_lab_token ON meme_lab (token_key);
CREATE INDEX IF NOT EXISTS idx_meme_lab_score ON meme_lab (source, horizon, return_pct);
