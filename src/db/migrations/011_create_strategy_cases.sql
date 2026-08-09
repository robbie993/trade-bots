-- The strategy court — a dropped file, and the verdict on it.
--
-- One row per submitted strategy, holding the whole trial: the evidence
-- gathered from the file, every juror's finding, both advocates' cases, and
-- the judge's ruling. Nothing here deploys anything: an accepted strategy
-- becomes a *candidate* genome, and a candidate only reaches a firm through
-- the evolver beating the incumbent, or through a human.

CREATE TABLE IF NOT EXISTS strategy_cases (
    id SERIAL PRIMARY KEY,
    firm_id INT REFERENCES firms(id),
    file_path TEXT,
    file_name VARCHAR(255),
    file_sha256 VARCHAR(64),
    file_bytes INT DEFAULT 0,
    submitted_by VARCHAR(120),
    genome TEXT,                            -- JSON, as extracted from the file
    universe TEXT,                          -- JSON list of symbols
    evidence TEXT,                          -- JSON: the forensic reading
    findings TEXT,                          -- JSON: every juror's verdict
    prosecution TEXT,
    defence TEXT,
    ruling VARCHAR(20),                     -- accept | modify | reject
    ruling_reason TEXT,
    confidence DECIMAL(5,2) DEFAULT 0,
    fitness DECIMAL(14,4),
    genome_id INT,                          -- strategy_genomes row, if accepted
    status VARCHAR(20) DEFAULT 'ruled',     -- ruled | deployed | withdrawn
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_strategy_cases_ruling ON strategy_cases(ruling);
CREATE INDEX IF NOT EXISTS idx_strategy_cases_sha ON strategy_cases(file_sha256);
