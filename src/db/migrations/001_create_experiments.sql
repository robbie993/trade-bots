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
