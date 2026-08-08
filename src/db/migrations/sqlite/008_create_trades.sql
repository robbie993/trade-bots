CREATE TABLE IF NOT EXISTS trade_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_id INTEGER REFERENCES firms(id),
    symbol TEXT,
    side TEXT,
    quantity NUMERIC DEFAULT 0,
    reference_price NUMERIC DEFAULT 0,
    notional NUMERIC DEFAULT 0,
    confidence NUMERIC DEFAULT 0,
    signals TEXT,
    bull_case TEXT,
    bear_case TEXT,
    debate_winner TEXT,
    rationale TEXT,
    risk_verdict TEXT,
    risk_reason TEXT,
    ethics_verdict TEXT,
    ethics_reason TEXT,
    status TEXT DEFAULT 'proposed',
    as_of TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_proposals_firm ON trade_proposals(firm_id);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON trade_proposals(status);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id INTEGER REFERENCES trade_proposals(id),
    firm_id INTEGER REFERENCES firms(id),
    symbol TEXT,
    side TEXT,
    quantity NUMERIC DEFAULT 0,
    price NUMERIC DEFAULT 0,
    fee NUMERIC DEFAULT 0,
    slippage NUMERIC DEFAULT 0,
    cash_delta NUMERIC DEFAULT 0,
    realized_pnl NUMERIC DEFAULT 0,
    venue TEXT DEFAULT 'paper',
    as_of TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_fills_firm ON fills(firm_id);
