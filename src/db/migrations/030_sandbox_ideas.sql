-- The idea lab: every call a scanner makes, traded on paper in the sandbox.
-- See the SQLite mirror and src/trading/sandbox/ideas.py for the reasoning.
--
-- Short version: a reading on the signal board is a vote at one seat of one
-- firm's debate, and nothing ever checked whether following it would pay. The
-- lab takes each call as it is made, pretends to trade it for an hour, a day
-- and a week, and scores each scanner against holding SPY for the same time.
--
-- Its own table, written only through the sandbox writer: not `fills`, not
-- `positions`, nothing touching `cash`. A research desk must not be able to
-- put a figure into the ledger.
CREATE TABLE IF NOT EXISTS sandbox_ideas (
    id SERIAL PRIMARY KEY,
    publisher TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    horizon TEXT NOT NULL,
    score NUMERIC DEFAULT 0,
    confidence NUMERIC DEFAULT 0,
    note TEXT DEFAULT '',
    outside INTEGER DEFAULT 0,
    opened_bar TEXT NOT NULL,
    due_at TEXT NOT NULL,
    entry_price NUMERIC NOT NULL,
    entry_cost_bps NUMERIC DEFAULT 0,
    spy_entry NUMERIC,
    closed_bar TEXT,
    exit_price NUMERIC,
    exit_cost_bps NUMERIC,
    spy_exit NUMERIC,
    return_pct NUMERIC,
    spy_pct NUMERIC,
    pnl NUMERIC,
    closed_why TEXT,
    created_at TEXT DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
);

CREATE INDEX IF NOT EXISTS idx_sandbox_ideas_open ON sandbox_ideas (closed_bar, publisher, symbol);
CREATE INDEX IF NOT EXISTS idx_sandbox_ideas_score ON sandbox_ideas (publisher, horizon, closed_bar);

-- The lab reads the whole board for one bar on every tick ("every call made on
-- this bar"), a question the board's two indexes, by symbol and by publisher,
-- do not answer without scanning every reading ever published.
CREATE INDEX IF NOT EXISTS idx_signals_as_of ON signals (as_of);
