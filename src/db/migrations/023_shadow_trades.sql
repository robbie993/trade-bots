-- Shadow trades: what a desk would have done, recorded and never settled.
-- See the SQLite mirror for the full reasoning.
--
-- Short version: the village cannot write options and a $20,000 firm cannot
-- cash-secure a SPY put, so the one options strategy with a measured edge here
-- is both forbidden and unaffordable. A shadow desk records what it would have
-- bought at the ask and marks it each bar, accumulating the track record this
-- village is short of — 117 closed trades is the binding constraint on the
-- evolver, the strikes, the scorecard and the court alike.
--
-- Its own table on purpose: not `fills`, not `positions`, nothing touching
-- `cash`. The reconciliation identity is what makes every other number here
-- trustworthy and it broke twice on 31 August; a research desk must not be
-- able to put a figure into it.
--
-- `source` separates real fills from the desk's hypotheticals — seeding this
-- table with real Alpaca option fills is the only executed option history the
-- village has, and blending it with imagined trades would make every score a
-- mix of evidence and invention.
--
-- `entry_price` is the ask, `exit_price` the bid, and `spread_pct` is kept so
-- a run can be re-read against what it cost rather than what it hoped.

-- `arm` is what makes this desk learn faster than anything else in the village.
-- A firm evolves once every TRADE_EVOLVE_EVERY bars and produces one genome's
-- worth of evidence in that time. This desk evaluates several genomes against
-- the *same* chain on every bar and records what each would have written, so a
-- bar yields as many observations as there are arms. Only the `live` arm is
-- the desk's actual position; the rest are counterfactuals, which cost nothing
-- because none of this touches money.
--
CREATE TABLE IF NOT EXISTS shadow_trades (
    id SERIAL PRIMARY KEY,
    desk TEXT NOT NULL,
    source TEXT DEFAULT 'shadow',
    arm TEXT DEFAULT 'live',
    contract TEXT NOT NULL,
    underlying TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity NUMERIC DEFAULT 0,
    entry_price NUMERIC DEFAULT 0,
    entry_mid NUMERIC DEFAULT 0,
    spread_pct NUMERIC DEFAULT 0,
    exit_price NUMERIC,
    realized NUMERIC,
    reason TEXT,
    opened_bar TEXT,
    closed_bar TEXT,
    opened_at TEXT DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    closed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_shadow_desk ON shadow_trades (desk, closed_at);
CREATE INDEX IF NOT EXISTS idx_shadow_contract ON shadow_trades (contract);

CREATE INDEX IF NOT EXISTS idx_shadow_arm ON shadow_trades (desk, arm, closed_at);
