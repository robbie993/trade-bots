CREATE TABLE IF NOT EXISTS token_balances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_id INTEGER REFERENCES firms(id),
    firm_key TEXT UNIQUE NOT NULL,
    balance NUMERIC DEFAULT 0,
    lifetime_earned NUMERIC DEFAULT 0,
    lifetime_spent NUMERIC DEFAULT 0,
    title TEXT DEFAULT 'Novice',
    reputation NUMERIC DEFAULT 100,
    season INTEGER DEFAULT 1,
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS token_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_id INTEGER REFERENCES firms(id),
    firm_key TEXT,
    amount NUMERIC DEFAULT 0,
    balance_after NUMERIC DEFAULT 0,
    reason TEXT,
    category TEXT,
    season INTEGER DEFAULT 1,
    payload TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_token_events_firm ON token_events(firm_key);

CREATE TABLE IF NOT EXISTS bouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season INTEGER DEFAULT 1,
    challenger TEXT,
    opponent TEXT,
    challenger_score NUMERIC DEFAULT 0,
    opponent_score NUMERIC DEFAULT 0,
    metric TEXT,
    winner TEXT,
    stake NUMERIC DEFAULT 0,
    decided INTEGER DEFAULT 0,
    reason TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_bouts_season ON bouts(season);
