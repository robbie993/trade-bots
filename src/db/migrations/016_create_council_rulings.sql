-- The council's rulings — every decision the village made for itself.
--
-- The approval row in `human_approvals` records *that* a decision was granted.
-- This table records *why*: the panel that heard it, what each juror said, and
-- the state of the ledger at the moment it was decided. A body that can grant
-- itself capital and leaves no reviewable record is not oversight, it is a
-- rubber stamp with a database behind it.
--
-- `evidence` is the full CouncilEvidence dataclass as JSON, which is what makes
-- a ruling reproducible: the jurors are pure functions of it, so replaying a
-- stored row must produce the stored verdict. There is a test for exactly that.
--
-- Unlike flow_events this is *not* disposable. It is the audit trail for
-- autonomous decisions and nothing prunes it.

CREATE TABLE IF NOT EXISTS council_rulings (
    id SERIAL PRIMARY KEY,
    approval_id INTEGER REFERENCES human_approvals(id),
    action VARCHAR(40) NOT NULL,            -- allocate_capital | kill_firm | resume_firm
    firm_key VARCHAR(40),
    verdict VARCHAR(10) NOT NULL,           -- grant | refuse | defer
    reason VARCHAR(400),
    confidence NUMERIC(6,2) DEFAULT 0,
    for_weight NUMERIC(8,2) DEFAULT 0,
    against_weight NUMERIC(8,2) DEFAULT 0,
    findings TEXT,                          -- every juror, verbatim
    evidence TEXT,                          -- the ledger as the council saw it
    evidence_digest VARCHAR(64),            -- unchanged evidence is not reheard
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_council_rulings_firm ON council_rulings(firm_key, action);
CREATE INDEX IF NOT EXISTS idx_council_rulings_approval ON council_rulings(approval_id);
