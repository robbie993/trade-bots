-- Shadow trades: what a desk would have done, recorded and never settled.
--
-- The village cannot write options — the risk manager blocks it, because no
-- capital limit can size an unbounded loss — and a $20,000 firm cannot
-- cash-secure a single SPY put at a $766 underlying. So the one options
-- strategy with a measured edge in this project (30-year PutWrite, Sharpe 0.83
-- against SPY's 0.72) is both forbidden and unaffordable here, and the
-- permitted one, buying premium, is the one the 2026-07-31 insider study
-- found died on a 52% round-trip spread.
--
-- A shadow desk sidesteps all three. It records what it would have bought at
-- the ask, marks it each bar, and accumulates a track record — which is the
-- one thing this village is actually short of. 117 closed trades is the
-- binding constraint on the evolver, the strike system, the scanner scorecard
-- and the court alike.
--
-- **Its own table, on purpose.** Not `fills`, not `positions`, and nothing
-- that touches `cash`. The reconciliation identity — cash = allocation +
-- sum(cash_delta) — is what makes every other number in this system
-- trustworthy, and it broke twice on 31 August. A research desk that could
-- put a figure into that identity would be trading the ledger's credibility
-- for a backtest, which is a bad trade at any price.
--
-- `source` separates what a desk imagined from what an account actually did.
-- Seeding this table with 39 real Alpaca option fills is the only way the
-- village has ever had executed option trades to learn from; mixing them
-- with the desk's own hypotheticals under one label would make every later
-- score a blend of evidence and imagination.
--
-- `entry_price` is the **ask** and `exit_price` is the **bid**. Storing a mid
-- would make every result here a fiction, and `spread_pct` is recorded
-- alongside so a run can always be re-read against what it cost rather than
-- what it hoped.

-- `arm` is what makes this desk learn faster than anything else in the village.
-- A firm evolves once every TRADE_EVOLVE_EVERY bars and produces one genome's
-- worth of evidence in that time. This desk evaluates several genomes against
-- the *same* chain on every bar and records what each would have written, so a
-- bar yields as many observations as there are arms. Only the `live` arm is
-- the desk's actual position; the rest are counterfactuals, which cost nothing
-- because none of this touches money.
--
CREATE TABLE IF NOT EXISTS shadow_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    opened_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    closed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_shadow_desk ON shadow_trades (desk, closed_at);
CREATE INDEX IF NOT EXISTS idx_shadow_contract ON shadow_trades (contract);

CREATE INDEX IF NOT EXISTS idx_shadow_arm ON shadow_trades (desk, arm, closed_at);
