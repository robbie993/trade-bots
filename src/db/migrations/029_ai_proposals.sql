-- Changes an outside mind proposed to a firm's genome, and what the village
-- found when it tested them.
--
-- A firm that is given advice (migration 028) asks the same mind to turn it into
-- a bounded change to its own genes. That proposal is not adopted on anyone's
-- say-so: the worker backtests it against the firm's current genome on the
-- evolver's split, the early bars it was chosen on and a held-out tail it never
-- saw, and only a proposal that wins the held-out bars is filed to the council
-- as an `adopt_genome` approval. The council reads these numbers from this row,
-- not from the request, and a human can always see why each one went where it
-- did.

CREATE TABLE IF NOT EXISTS ai_proposals (
    id SERIAL PRIMARY KEY,
    firm_key VARCHAR(80) NOT NULL,
    question_id INTEGER,
    proposed_by VARCHAR(120) DEFAULT '',
    why TEXT DEFAULT '',
    changes TEXT NOT NULL,
    genome_before TEXT DEFAULT '{}',
    genome_proposed TEXT DEFAULT '{}',
    status VARCHAR(20) DEFAULT 'awaiting_test',
    fitted_before DECIMAL(14,4),
    fitted_after DECIMAL(14,4),
    holdout_before DECIMAL(14,4),
    holdout_after DECIMAL(14,4),
    holdout_bars INTEGER,
    verdict TEXT DEFAULT '',
    approval_id INTEGER,
    created_at TEXT DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    tested_at TEXT
);
