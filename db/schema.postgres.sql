-- MVV Phase 1 — "The Experiment Ledger"
-- PostgreSQL schema. Applied by `python -m mvv init-db`.
--
-- Tables 1-3 are the spec schema (sections 3.1-3.3) with additive columns only.
-- Tables 4-5 (approvals, experiment_events) are Phase 1 additions required by
-- spec section 6 (human approval gates) and the "auditable" requirement.

CREATE TABLE IF NOT EXISTS experiments (
    id SERIAL PRIMARY KEY,
    product_id VARCHAR(50) UNIQUE,
    product_name TEXT,
    source_platform VARCHAR(20),        -- 'alibaba', 'aliexpress', 'temu', 'amazon', 'etsy'
    supplier VARCHAR(100),
    product_url TEXT,
    unit_cost DECIMAL(10,2),            -- COGS per unit (landed)
    selling_price DECIMAL(10,2),        -- Price on your store
    margin_pct DECIMAL(5,2),            -- (selling_price - unit_cost) / selling_price * 100
    margin_abs DECIMAL(10,2),           -- selling_price - unit_cost

    -- Traffic metrics (raw)
    impressions INT DEFAULT 0,
    clicks INT DEFAULT 0,
    sessions INT DEFAULT 0,

    -- Order metrics
    orders INT DEFAULT 0,
    revenue DECIMAL(12,2) DEFAULT 0,
    ad_spend DECIMAL(12,2) DEFAULT 0,

    -- Derived (computed by the Economic Calculator, persisted for audit)
    ctr DECIMAL(5,2) DEFAULT 0,                  -- clicks/impressions * 100
    conversion_rate DECIMAL(5,2) DEFAULT 0,      -- orders/sessions * 100
    cac DECIMAL(10,2) DEFAULT 0,                 -- ad_spend / orders
    aov DECIMAL(10,2) DEFAULT 0,                 -- revenue / orders
    contribution_margin DECIMAL(10,2) DEFAULT 0, -- revenue - unit_cost*orders - ad_spend

    -- Quality metrics
    refunds INT DEFAULT 0,
    chargebacks INT DEFAULT 0,
    refund_rate DECIMAL(5,2) DEFAULT 0,          -- refunds/orders * 100
    chargeback_rate DECIMAL(5,2) DEFAULT 0,
    avg_delivery_days DECIMAL(5,2) DEFAULT 0,

    -- Sample-size gates (for decision validity)
    minimum_orders INT DEFAULT 50,
    minimum_sessions INT DEFAULT 100,
    minimum_impressions INT DEFAULT 1000,
    meets_sample_gate BOOLEAN DEFAULT FALSE,

    -- Ad state. The system may pause ads autonomously; it may never start or
    -- raise spend without an approved approval row.
    ads_paused BOOLEAN DEFAULT TRUE,
    daily_ad_budget DECIMAL(10,2) DEFAULT 0,

    -- Status & kill decision
    status VARCHAR(20) DEFAULT 'discovered', -- discovered, sampled, launched, active, killed, scaling, success
    kill_reason VARCHAR(200),
    pending_kill_reason VARCHAR(200),        -- trigger fired, awaiting human decision
    kill_override_reason VARCHAR(200),       -- human answered CONTINUE to this reason
    killed_at TIMESTAMPTZ,
    scaled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status);

CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    experiment_id INT REFERENCES experiments(id),
    external_id VARCHAR(64) UNIQUE,
    order_date TIMESTAMPTZ DEFAULT NOW(),
    customer_paid DECIMAL(10,2),
    supplier_cost DECIMAL(10,2),
    shipping_cost DECIMAL(10,2),
    ad_cost DECIMAL(10,2),                  -- attributed ad spend for this order
    payment_processor_fee DECIMAL(10,2),
    refunded BOOLEAN DEFAULT FALSE,
    refund_date TIMESTAMPTZ,
    chargeback BOOLEAN DEFAULT FALSE,
    delivered BOOLEAN DEFAULT FALSE,
    delivery_date TIMESTAMPTZ,
    days_to_delivery INT
);

CREATE INDEX IF NOT EXISTS idx_orders_experiment ON orders(experiment_id);

CREATE TABLE IF NOT EXISTS cash_flow (
    id SERIAL PRIMARY KEY,
    date TIMESTAMPTZ DEFAULT NOW(),
    description TEXT,
    category VARCHAR(30),  -- 'ad_spend', 'supplier_payment', 'customer_revenue',
                           -- 'processor_hold', 'refund', 'opening_balance', 'other'
    amount DECIMAL(12,2),  -- signed: credits positive, debits negative
    hold BOOLEAN DEFAULT FALSE,  -- money exists but is not yet spendable
    release_date TIMESTAMPTZ,
    experiment_id INT REFERENCES experiments(id),
    order_id INT REFERENCES orders(id)
);

CREATE INDEX IF NOT EXISTS idx_cash_flow_date ON cash_flow(date);

-- Human Approval Gate (spec section 6). Nothing that costs money or changes the
-- public store may happen without an approved row here.
CREATE TABLE IF NOT EXISTS approvals (
    id SERIAL PRIMARY KEY,
    action VARCHAR(40),        -- see mvv.human_gate.ApprovalAction
    experiment_id INT REFERENCES experiments(id),
    amount DECIMAL(12,2),
    reason TEXT,
    details TEXT,              -- JSON payload describing the exact request
    status VARCHAR(20) DEFAULT 'pending',  -- pending, approved, rejected, expired
    requested_at TIMESTAMPTZ DEFAULT NOW(),
    decided_at TIMESTAMPTZ,
    decided_by VARCHAR(100),
    decision_notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);

-- Append-only audit trail. Every state change writes a row here.
CREATE TABLE IF NOT EXISTS experiment_events (
    id SERIAL PRIMARY KEY,
    experiment_id INT REFERENCES experiments(id),
    event_type VARCHAR(40),
    detail TEXT,
    payload TEXT,             -- JSON snapshot
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_experiment ON experiment_events(experiment_id);
