-- Human Approvals Log — spec section 3.4.
--
-- The hard boundary of spec section 6, expressed as a table. Nothing that
-- costs money or changes the storefront happens without a row here whose
-- status a human changed.
--
-- Spec columns are used verbatim. `approved_at` / `approved_by` record when
-- the decision was made and by whom *whatever the outcome was* — `status`
-- distinguishes approved from rejected, so a rejection is timestamped and
-- attributed too rather than silently losing its author.
--
-- Additive columns beyond the spec: `amount` (an approved figure is a
-- ceiling, not a suggestion — see HumanGate.require_approval) and `details`
-- (the JSON payload of exactly what was asked, so an approval can be audited
-- against what it authorised).

CREATE TABLE IF NOT EXISTS human_approvals (
    id SERIAL PRIMARY KEY,
    experiment_id INT REFERENCES experiments(id),
    action VARCHAR(50),        -- 'sample_order', 'launch', 'kill', 'scale',
                               -- 'ad_spend', 'add_platform', 'kill_all'
    requested_at TIMESTAMPTZ DEFAULT NOW(),
    approved_at TIMESTAMPTZ,
    approved_by VARCHAR(100),
    status VARCHAR(20) DEFAULT 'pending', -- pending, approved, rejected
    notes TEXT,

    amount DECIMAL(12,2),      -- spend ceiling this approval authorises
    reason TEXT,               -- why the system asked
    details TEXT               -- JSON payload describing the exact request
);

CREATE INDEX IF NOT EXISTS idx_human_approvals_status ON human_approvals(status);
CREATE INDEX IF NOT EXISTS idx_human_approvals_experiment ON human_approvals(experiment_id);
