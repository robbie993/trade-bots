-- Every trade leaves two rows: what was proposed, and what actually happened.
--
-- A proposal is written *before* anything executes, with the debate that
-- produced it, the risk verdict and the ethics verdict attached. A proposal
-- that is blocked stays in the table with its reason. The audit question
-- "why did this firm buy that?" is answered by a row, not by a log line.

CREATE TABLE IF NOT EXISTS trade_proposals (
    id SERIAL PRIMARY KEY,
    firm_id INT REFERENCES firms(id),
    symbol VARCHAR(40),
    side VARCHAR(10),                       -- buy | sell
    quantity DECIMAL(18,8) DEFAULT 0,
    reference_price DECIMAL(18,8) DEFAULT 0,
    notional DECIMAL(14,2) DEFAULT 0,
    confidence DECIMAL(5,2) DEFAULT 0,
    signals TEXT,                           -- JSON: every analyst's reading
    bull_case TEXT,
    bear_case TEXT,
    debate_winner VARCHAR(10),
    rationale TEXT,
    risk_verdict VARCHAR(20),               -- allow | resize | block
    risk_reason TEXT,
    ethics_verdict VARCHAR(20),             -- allow | warn | block
    ethics_reason TEXT,
    status VARCHAR(20) DEFAULT 'proposed',  -- proposed | filled | rejected | blocked
    as_of TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_proposals_firm ON trade_proposals(firm_id);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON trade_proposals(status);

CREATE TABLE IF NOT EXISTS fills (
    id SERIAL PRIMARY KEY,
    proposal_id INT REFERENCES trade_proposals(id),
    firm_id INT REFERENCES firms(id),
    symbol VARCHAR(40),
    side VARCHAR(10),
    quantity DECIMAL(18,8) DEFAULT 0,
    price DECIMAL(18,8) DEFAULT 0,
    fee DECIMAL(14,2) DEFAULT 0,
    slippage DECIMAL(14,2) DEFAULT 0,
    cash_delta DECIMAL(14,2) DEFAULT 0,     -- signed: what left or entered cash
    realized_pnl DECIMAL(14,2) DEFAULT 0,
    venue VARCHAR(40) DEFAULT 'paper',
    as_of TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fills_firm ON fills(firm_id);
