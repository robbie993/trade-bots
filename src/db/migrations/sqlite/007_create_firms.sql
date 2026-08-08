CREATE TABLE IF NOT EXISTS firms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_key TEXT UNIQUE NOT NULL,
    name TEXT,
    asset_class TEXT,
    strategy TEXT,
    venue TEXT DEFAULT 'paper',
    status TEXT DEFAULT 'active',
    risk_limit NUMERIC DEFAULT 0.02,
    initial_allocation NUMERIC DEFAULT 0,
    allocation NUMERIC DEFAULT 0,
    cash NUMERIC DEFAULT 0,
    high_water_mark NUMERIC DEFAULT 0,
    consecutive_losses INTEGER DEFAULT 0,
    genome TEXT,
    universe TEXT,
    kill_reason TEXT,
    killed_at TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_firms_status ON firms(status);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_id INTEGER REFERENCES firms(id),
    symbol TEXT,
    quantity NUMERIC DEFAULT 0,
    avg_price NUMERIC DEFAULT 0,
    realized_pnl NUMERIC DEFAULT 0,
    opened_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_positions_firm ON positions(firm_id);
