-- Trading Edition — the firms the brokerage allocates to.
--
-- A firm is the trading analogue of a store: an independent pod with its own
-- capital, its own risk limit and its own kill switch. `allocation` is what
-- the brokerage has entrusted to it; `cash` is what is uninvested right now.
-- Both are DECIMAL for the same reason every other money column is: a ledger
-- that drifts by fractions of a cent cannot be reconciled, and this system
-- refuses to act on a ledger that does not reconcile.

CREATE TABLE IF NOT EXISTS firms (
    id SERIAL PRIMARY KEY,
    firm_key VARCHAR(40) UNIQUE NOT NULL,
    name VARCHAR(120),
    asset_class VARCHAR(40),
    strategy VARCHAR(60),
    venue VARCHAR(40) DEFAULT 'paper',
    status VARCHAR(20) DEFAULT 'active',      -- active | paused | killed
    risk_limit DECIMAL(6,4) DEFAULT 0.02,     -- fraction of allocation per trade
    initial_allocation DECIMAL(14,2) DEFAULT 0,
    allocation DECIMAL(14,2) DEFAULT 0,
    cash DECIMAL(14,2) DEFAULT 0,
    high_water_mark DECIMAL(14,2) DEFAULT 0,
    consecutive_losses INT DEFAULT 0,
    genome TEXT,                              -- JSON strategy parameters
    universe TEXT,                            -- JSON list of symbols
    kill_reason TEXT,
    killed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_firms_status ON firms(status);

-- Open positions, one row per firm/symbol. Closed positions have quantity 0
-- and are kept so the position history is auditable.
CREATE TABLE IF NOT EXISTS positions (
    id SERIAL PRIMARY KEY,
    firm_id INT REFERENCES firms(id),
    symbol VARCHAR(40),
    quantity DECIMAL(18,8) DEFAULT 0,
    avg_price DECIMAL(18,8) DEFAULT 0,
    realized_pnl DECIMAL(14,2) DEFAULT 0,
    opened_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_positions_firm ON positions(firm_id);
