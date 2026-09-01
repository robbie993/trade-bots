CREATE TABLE IF NOT EXISTS genome_certificates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_id INTEGER REFERENCES firms(id),
    firm_key TEXT,
    genome TEXT,
    fingerprint TEXT,
    data_source TEXT,
    data_fingerprint TEXT,
    bars INTEGER DEFAULT 0,
    folds INTEGER DEFAULT 0,
    folds_passed INTEGER DEFAULT 0,
    verdict INTEGER DEFAULT 0,
    in_sample_fitness NUMERIC,
    oos_fitness NUMERIC,
    oos_return_pct NUMERIC,
    oos_max_drawdown_pct NUMERIC,
    oos_sharpe NUMERIC,
    oos_trades INTEGER DEFAULT 0,
    reasons TEXT,
    report TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_certificates_fingerprint ON genome_certificates(fingerprint);
CREATE INDEX IF NOT EXISTS idx_certificates_firm ON genome_certificates(firm_key);
