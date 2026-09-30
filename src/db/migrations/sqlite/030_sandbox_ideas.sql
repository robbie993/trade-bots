-- The idea lab: every call a scanner makes, traded on paper in the sandbox.
--
-- A reading on the signal board is a vote. It joins one seat of one firm's
-- debate, is averaged with the other seats, and whatever the firm then does is
-- the firm's result, not the scanner's. So nothing in the village could say
-- whether following a scanner would have paid: its calls were heard and never
-- tested, and the names outside the village's universe (the Form 4 small caps,
-- the fleet's altcoins) were dropped without even being heard.
--
-- The lab tests each call on its own. When a scanner says buy or sell, the lab
-- pretends to trade it at the price a firm would have got on that bar, pays
-- what crossing really costs then, holds it for each horizon (an hour, a day, a
-- week) and records what it made next to what holding SPY made over the same
-- window. A scanner repeating the same call every bar is one idea per horizon
-- until that idea closes; a scanner that flips closes its idea early.
--
-- **Its own table, on purpose.** Not `fills`, not `positions`, and nothing that
-- touches `cash`, the same rule as `shadow_trades`. It is written only through
-- the sandbox writer, which refuses every other table.
--
-- `outside` marks a call on a symbol the village does not trade. Those are
-- priced from Alpaca's latest bars rather than the village's feed, and kept
-- separate so the two can be read apart.
--
-- `return_pct` is net of both sides' costs and in the call's direction (a sell
-- call made money when the price fell). `spy_pct` is SPY over the same window
-- with no costs, which is the stricter yardstick: somebody simply holding SPY
-- pays nothing per window. `pnl` is `return_pct` on a notional $1,000.
CREATE TABLE IF NOT EXISTS sandbox_ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_sandbox_ideas_open ON sandbox_ideas (closed_bar, publisher, symbol);
CREATE INDEX IF NOT EXISTS idx_sandbox_ideas_score ON sandbox_ideas (publisher, horizon, closed_bar);

-- The lab reads the whole board for one bar on every tick ("every call made on
-- this bar"), a question the board's two indexes, by symbol and by publisher,
-- do not answer without scanning every reading ever published.
CREATE INDEX IF NOT EXISTS idx_signals_as_of ON signals (as_of);
