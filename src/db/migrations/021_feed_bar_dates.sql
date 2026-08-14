-- One row per firm per feed per *market bar*, replacing a counter that
-- counted ticks.
--
-- Migration 020 stored `firm_feeds.bars` as an integer incremented once every
-- time a scorecard was written. A scorecard is written once per tick, and the
-- loop ticks every sixty seconds regardless of whether the market has moved,
-- so a village left running for two hours on a daily feed reported 136 "bars"
-- against a feed whose date had advanced once. The promotion gate then read
-- that number and cleared the check it exists to enforce.
--
-- That is the same mistake as the Sharpe kill: **counting ticks as though they
-- were new information**. It was worse here, because this is the check that a
-- track record cannot fake, guarding the decision that spends real money — and
-- it was fakeable by leaving a process running over lunch.
--
-- So the bar itself is now the key. Recording is idempotent by construction:
-- ticking a thousand times against the same bar inserts one row, because the
-- primary key already holds it. `bars_on` counts rows, and the count means
-- what the criterion always claimed it meant — distinct market bars observed
-- while that feed was priced.
--
-- `bar_date` is the market's own `as_of`, not the wall clock, for the same
-- reason the living layer uses it: replaying the same forty-five days twice
-- must produce the same history. A tick where the market cannot say what bar
-- it is on records nothing at all — an unknown bar is not evidence, and this
-- is a file about what counts as evidence.
--
-- Nothing here is ever deleted. A firm's history on a feed it no longer uses
-- is still true, and quietly dropping it would let a firm re-qualify by
-- switching feeds twice.

CREATE TABLE IF NOT EXISTS firm_feed_bars (
    firm_id INTEGER NOT NULL,
    priced_by VARCHAR(40) NOT NULL,
    bar_date VARCHAR(32) NOT NULL,
    recorded_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (firm_id, priced_by, bar_date)
);

CREATE INDEX IF NOT EXISTS idx_firm_feed_bars_lookup
    ON firm_feed_bars (firm_id, priced_by);

-- The old counter is dropped rather than left in place. Its column is called
-- `bars` and holds a number of ticks, and a wrong number that looks right is
-- how the next reader makes the same mistake. `DROP TABLE IF EXISTS` is
-- idempotent in both dialects, which every migration here has to be.
DROP TABLE IF EXISTS firm_feeds;
