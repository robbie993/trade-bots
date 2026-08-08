-- The File Court docket.
--
-- One row per file that has been through the court. Additive: this migration
-- touches none of 001-005, so the Experiment Ledger schema is unchanged.
--
-- The three JSON columns are TEXT rather than JSONB on purpose. src/db is a
-- dialect-agnostic layer that rewrites `?` placeholders and passes Python
-- values straight through; handing psycopg a `str` for a JSONB column fails
-- without an explicit ::jsonb cast, which the shared query path has no way to
-- add. Migration 005 already set this precedent with `payload TEXT`. The
-- payloads are still json.dumps()'d, so `evidence::jsonb` works in any query
-- that wants it.
CREATE TABLE IF NOT EXISTS file_cases (
    id SERIAL PRIMARY KEY,
    file_name TEXT,
    file_path TEXT,
    file_hash VARCHAR(64),            -- sha256, so a re-upload is recognisable
    verdict VARCHAR(20),              -- GUILTY | MIXED | NOT_GUILTY | UNREVIEWED
    risk_score DECIMAL(5,2),          -- 0-100, or NULL when nothing scored it
    summary TEXT,
    backends TEXT,                    -- JSON: which reviewers ran, and outcome
    evidence TEXT,                    -- JSON
    investigation TEXT,               -- JSON
    trial_transcript TEXT,            -- JSON
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_file_cases_verdict ON file_cases(verdict);
CREATE INDEX IF NOT EXISTS idx_file_cases_hash ON file_cases(file_hash);
