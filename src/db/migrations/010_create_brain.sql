-- The Brain: what happened, and what to try next.
--
-- trade_memory is the recall surface — one row per closed trade or notable
-- event, queryable by symbol so a firm can ask "how has this gone before?"
-- strategy_genomes is the evolution surface — parameter sets, their measured
-- fitness, and which parent they came from. Both are plain tables on purpose:
-- an embedding store you cannot read in SQL is not an audit trail.

CREATE TABLE IF NOT EXISTS trade_memory (
    id SERIAL PRIMARY KEY,
    firm_id INT REFERENCES firms(id),
    symbol VARCHAR(40),
    memory_type VARCHAR(40),                -- trade | lesson | regime
    summary TEXT,
    payload TEXT,                           -- JSON snapshot
    outcome VARCHAR(20),                    -- win | loss | flat
    reward DECIMAL(14,2) DEFAULT 0,
    tags TEXT,
    as_of TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memory_symbol ON trade_memory(symbol);
CREATE INDEX IF NOT EXISTS idx_memory_firm ON trade_memory(firm_id);

CREATE TABLE IF NOT EXISTS strategy_genomes (
    id SERIAL PRIMARY KEY,
    firm_id INT REFERENCES firms(id),
    generation INT DEFAULT 0,
    genome TEXT,                            -- JSON parameters
    fitness DECIMAL(14,4),
    trades INT DEFAULT 0,
    parent_id INT,
    selected BOOLEAN DEFAULT FALSE,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_genomes_firm ON strategy_genomes(firm_id);
