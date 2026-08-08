-- The brokerage's scorecard, and the record of what it did about it.
--
-- One firm_performance row per firm per evaluation. The score that justified
-- an allocation change is stored next to the inputs that produced it, so a
-- capital cut can be re-derived months later from the row alone.

CREATE TABLE IF NOT EXISTS firm_performance (
    id SERIAL PRIMARY KEY,
    firm_id INT REFERENCES firms(id),
    as_of TIMESTAMPTZ,
    equity DECIMAL(14,2) DEFAULT 0,
    cash DECIMAL(14,2) DEFAULT 0,
    market_value DECIMAL(14,2) DEFAULT 0,
    realized_pnl DECIMAL(14,2) DEFAULT 0,
    unrealized_pnl DECIMAL(14,2) DEFAULT 0,
    fees DECIMAL(14,2) DEFAULT 0,
    capital_base DECIMAL(14,2) DEFAULT 0,
    return_pct DECIMAL(9,4) DEFAULT 0,
    drawdown_pct DECIMAL(9,4) DEFAULT 0,
    win_rate_pct DECIMAL(9,4) DEFAULT 0,
    sharpe DECIMAL(9,4),
    trades INT DEFAULT 0,
    consecutive_losses INT DEFAULT 0,
    worst_trade_pct DECIMAL(9,4) DEFAULT 0,
    score DECIMAL(9,4) DEFAULT 0,
    sufficient_data BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_performance_firm ON firm_performance(firm_id);

CREATE TABLE IF NOT EXISTS brokerage_events (
    id SERIAL PRIMARY KEY,
    firm_id INT REFERENCES firms(id),
    event_type VARCHAR(40),                 -- allocation | kill | reconcile | halt
    detail TEXT,
    payload TEXT,                           -- JSON snapshot
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_brokerage_events_firm ON brokerage_events(firm_id);
