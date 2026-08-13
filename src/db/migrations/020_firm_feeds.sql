-- Which feed a firm's track record was measured on, and for how many bars.
--
-- The question this exists to answer is the one that stands between a paper
-- village and real money: **has this firm been measured on the feed it is
-- about to trade?**
--
-- It is not rhetorical. This village ran for weeks on a seeded random walk,
-- and a strategy's record against invented prices is not evidence about
-- anything — the walk has no gaps, no halts, no earnings, no correlation and
-- no liquidity. A firm with a beautiful synthetic Sharpe has demonstrated that
-- it can trade a random walk, which nobody is offering to pay for.
--
-- `position_provenance` (migration 019) answers the same question about *open
-- positions*, and answers it well enough to refuse a kill. This one is about
-- the *history*, which is a different span: a firm can be flat today and still
-- have a hundred bars of record behind it, and promotion cares about all of
-- them.
--
-- One row per firm per feed. `bars` counts scorecards written while that feed
-- was in use, so "32 bars on alpaca, 180 on synthetic" is a fact the
-- eligibility check reads rather than an assumption it makes.
--
-- Nothing here is ever deleted. A firm's history on a feed it no longer uses
-- is still true, and quietly dropping it would let a firm re-qualify by
-- switching feeds twice.

CREATE TABLE IF NOT EXISTS firm_feeds (
    firm_id INTEGER NOT NULL,
    priced_by VARCHAR(40) NOT NULL,
    bars INTEGER DEFAULT 0,
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_seen TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (firm_id, priced_by)
);
