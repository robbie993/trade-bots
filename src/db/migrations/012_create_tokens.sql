-- Tokens — the scoreboard, and emphatically not the money.
--
-- Tokens live in their own table with their own ledger and have no exchange
-- rate to capital. That separation is the whole point: capital moves only
-- through the allocator, where a human gates every increase, and a currency
-- the firms can award themselves must never become a second route to it.
--
-- Balances are DECIMAL for consistency with the rest of the schema, but they
-- are points. Nothing in src/trading/brokerage reads this table.

CREATE TABLE IF NOT EXISTS token_balances (
    id SERIAL PRIMARY KEY,
    firm_id INT REFERENCES firms(id),
    firm_key VARCHAR(40) UNIQUE NOT NULL,
    balance DECIMAL(14,2) DEFAULT 0,
    lifetime_earned DECIMAL(14,2) DEFAULT 0,
    lifetime_spent DECIMAL(14,2) DEFAULT 0,
    title VARCHAR(40) DEFAULT 'Novice',
    reputation DECIMAL(9,2) DEFAULT 100,
    season INT DEFAULT 1,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS token_events (
    id SERIAL PRIMARY KEY,
    firm_id INT REFERENCES firms(id),
    firm_key VARCHAR(40),
    amount DECIMAL(14,2) DEFAULT 0,         -- signed
    balance_after DECIMAL(14,2) DEFAULT 0,
    reason TEXT,
    category VARCHAR(40),                   -- bout | milestone | market | penalty
    season INT DEFAULT 1,
    payload TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_token_events_firm ON token_events(firm_key);

CREATE TABLE IF NOT EXISTS bouts (
    id SERIAL PRIMARY KEY,
    season INT DEFAULT 1,
    challenger VARCHAR(40),
    opponent VARCHAR(40),
    challenger_score DECIMAL(9,4) DEFAULT 0,
    opponent_score DECIMAL(9,4) DEFAULT 0,
    metric VARCHAR(40),
    winner VARCHAR(40),
    stake DECIMAL(14,2) DEFAULT 0,
    decided BOOLEAN DEFAULT FALSE,
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bouts_season ON bouts(season);
