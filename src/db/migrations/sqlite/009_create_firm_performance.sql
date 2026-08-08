CREATE TABLE IF NOT EXISTS firm_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_id INTEGER REFERENCES firms(id),
    as_of TEXT,
    equity NUMERIC DEFAULT 0,
    cash NUMERIC DEFAULT 0,
    market_value NUMERIC DEFAULT 0,
    realized_pnl NUMERIC DEFAULT 0,
    unrealized_pnl NUMERIC DEFAULT 0,
    fees NUMERIC DEFAULT 0,
    capital_base NUMERIC DEFAULT 0,
    return_pct NUMERIC DEFAULT 0,
    drawdown_pct NUMERIC DEFAULT 0,
    win_rate_pct NUMERIC DEFAULT 0,
    sharpe NUMERIC,
    trades INTEGER DEFAULT 0,
    consecutive_losses INTEGER DEFAULT 0,
    worst_trade_pct NUMERIC DEFAULT 0,
    score NUMERIC DEFAULT 0,
    sufficient_data INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_performance_firm ON firm_performance(firm_id);

CREATE TABLE IF NOT EXISTS brokerage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_id INTEGER REFERENCES firms(id),
    event_type TEXT,
    detail TEXT,
    payload TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_brokerage_events_firm ON brokerage_events(firm_id);
