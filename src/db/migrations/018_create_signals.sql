-- Published readings — what the scanners saw, on the bar they saw it.
--
-- A lot of what people call a trading bot is not a strategy at all. It scans,
-- it ranks, it watches large wallets, it shouts a ticker. Run through the
-- strategy adapter such a thing is useless: it never returns an order, so the
-- firm proposes nothing and the file looks broken when it is working exactly
-- as written.
--
-- This is the seat those bots actually want. A scanner publishes a *reading* —
-- a symbol, a score from -100 to +100, a confidence from 0 to 100 — and a firm
-- that lists the `signals` analyst hears it alongside its own technical,
-- fundamental and macro seats. It informs a decision; it does not make one.
--
-- What a scanner cannot do is the point of storing this separately from
-- `trade_proposals`: there is no path from a row here to an order. The score
-- goes into a debate, the debate goes to the trader, the trader's proposal
-- goes to the risk manager, the conscience and the gate — the same road every
-- other proposal travels. A scanner that publishes +100 on everything moves
-- one vote at one seat and buys nothing.
--
-- `as_of` is the market bar the reading was taken on, not the wall clock, for
-- the same reason the kill criteria count bars: two readings published four
-- minutes apart on the same daily bar are one reading, and a backtest must be
-- able to replay this table.
--
-- **Freshness has no grace period.** The analyst reads only rows stamped with
-- the bar it is currently on. A scanner that crashed, was deleted, or was
-- never wired up goes silent on the very next bar rather than voting from the
-- grave. Confidence 0 is silence, not neutrality — the same distinction the
-- analysts make everywhere else.

CREATE TABLE IF NOT EXISTS signals (
    id SERIAL PRIMARY KEY,
    publisher VARCHAR(60) NOT NULL,         -- the scanner's name in config
    symbol VARCHAR(40) NOT NULL,
    score NUMERIC(8,2) DEFAULT 0,           -- -100 bearish .. +100 bullish
    confidence NUMERIC(8,2) DEFAULT 0,      -- 0 = silence, not neutrality
    note VARCHAR(400),                      -- why, in the scanner's own words
    as_of VARCHAR(40),                      -- the market bar, not the clock
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol, id);
CREATE INDEX IF NOT EXISTS idx_signals_publisher ON signals(publisher, as_of);
