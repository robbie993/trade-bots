-- The crucible: what a genome proved, on data it was never allowed to see.
--
-- One row per gauntlet. A certificate is bound to a *fingerprint* of the
-- genome, not to a firm: change one gene and the fingerprint changes, the
-- certificate no longer matches, and the live gate closes again. That is the
-- whole mechanism — a strategy cannot mutate its way onto real money.
--
-- Failed gauntlets are stored too. "This genome was tried and could not
-- survive out of sample" is the more useful half of the record, and deleting
-- it would let the same idea be re-proposed forever.

CREATE TABLE IF NOT EXISTS genome_certificates (
    id SERIAL PRIMARY KEY,
    firm_id INT REFERENCES firms(id),
    firm_key VARCHAR(60),
    genome TEXT,                            -- JSON parameters, as certified
    fingerprint VARCHAR(64),                -- sha256 of the canonical genome
    data_source VARCHAR(40),                -- the feed the proof was earned on
    data_fingerprint VARCHAR(64),           -- source + symbols + bar count
    bars INT DEFAULT 0,
    folds INT DEFAULT 0,
    folds_passed INT DEFAULT 0,
    verdict BOOLEAN DEFAULT FALSE,
    in_sample_fitness DECIMAL(14,4),
    oos_fitness DECIMAL(14,4),
    oos_return_pct DECIMAL(14,4),
    oos_max_drawdown_pct DECIMAL(14,4),
    oos_sharpe DECIMAL(14,4),
    oos_trades INT DEFAULT 0,
    reasons TEXT,                           -- why it failed, in English
    report TEXT,                            -- JSON: every fold, both windows
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_certificates_fingerprint ON genome_certificates(fingerprint);
CREATE INDEX IF NOT EXISTS idx_certificates_firm ON genome_certificates(firm_key);
