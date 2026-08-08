CREATE TABLE IF NOT EXISTS trade_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_id INTEGER REFERENCES firms(id),
    symbol TEXT,
    memory_type TEXT,
    summary TEXT,
    payload TEXT,
    outcome TEXT,
    reward NUMERIC DEFAULT 0,
    tags TEXT,
    as_of TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_memory_symbol ON trade_memory(symbol);
CREATE INDEX IF NOT EXISTS idx_memory_firm ON trade_memory(firm_id);

CREATE TABLE IF NOT EXISTS strategy_genomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_id INTEGER REFERENCES firms(id),
    generation INTEGER DEFAULT 0,
    genome TEXT,
    fitness NUMERIC,
    trades INTEGER DEFAULT 0,
    parent_id INTEGER,
    selected INTEGER DEFAULT 0,
    notes TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_genomes_firm ON strategy_genomes(firm_id);
